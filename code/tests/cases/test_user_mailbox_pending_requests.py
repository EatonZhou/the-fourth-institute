"""User Mailbox projects the question owner's current answerable requests.

:module: workbench.chat
:importance: P1
:design: decision_15f7df737ed5
"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import ContextVar
from threading import Barrier, Event

import pytest

from management_portal import society_service, web_isa
from management_portal.http_timing import capture_timings


SOURCE = "resident_111111111111"
FACADE = "resident_222222222222"


def request():
    return {"key": f"request:{SOURCE}:msg_question", "source_resident_id": SOURCE,
            "source_message_id": "msg_question", "resident_id": FACADE,
            "message_id": "msg_question", "kind": "question_set",
            "created_at": "2026-11-11T00:00:00Z", "title": "Assistant",
            "questions": [{"title": "Private question"}], "answers": {"0": "Private answer"}}


@pytest.fixture
def mailbox(monkeypatch):
    current = [request()]
    calls = []

    def pending(actor):
        calls.append(actor)
        return {"requests": current}

    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(list_user_pending_requests=pending)))
    monkeypatch.setattr(web_isa, "_named_user", lambda inv: inv.argument("user_id"))
    # Empty/read/cleared conversation rows must not erase unanswered questions.
    monkeypatch.setattr(web_isa, "_inbox_entries", lambda *_: [])

    def read(**arguments):
        return web_isa.get_user_mailbox(SimpleNamespace(
            caller="authorized-mailbox-reader", argument={"user_id": "CASE-A", **arguments}.get))

    return current, calls, read


def test_pending_requests_survive_empty_read_mailbox_without_counting_as_unread(mailbox):
    current, calls, read = mailbox
    value = read()
    assert value["items"] == [] and value["unread_count"] == 0
    assert calls == ["CASE-A"]
    pending = value["pending_requests"][0]
    assert pending["key"] == request()["key"]
    assert "questions" not in pending and "answers" not in pending
    assert parse_qs(urlsplit(pending["url"]).query) == {
        "resident": [FACADE], "open_chat": ["1"], "message": ["msg_question"]}
    # The next owner read has answered/cancelled/expired or revoked this request.
    current.clear()
    assert read()["pending_requests"] == []


@pytest.mark.parametrize("related,expected", [(SOURCE, 1), (FACADE, 1), ("resident_333333333333", 0)])
def test_pending_requests_respect_related_resident_including_family_source(mailbox, related, expected):
    assert len(mailbox[2](related_resident_id=related)["pending_requests"]) == expected


def test_failed_question_read_is_not_reported_as_an_empty_pending_list(mailbox, monkeypatch):
    def unavailable(_actor):
        raise RuntimeError("Question owner unavailable")

    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(list_user_pending_requests=unavailable)))
    with pytest.raises(RuntimeError, match="Question owner unavailable"):
        mailbox[2]()


def test_independent_reads_overlap_without_mixing_users_or_request_context(mailbox, monkeypatch):
    context = ContextVar("mailbox-test-user")
    both_reads = {user: Barrier(2) for user in ("CASE-A", "CASE-B")}

    def items(actor, related):
        assert context.get() == actor
        both_reads[actor].wait(timeout=5)
        return [{"key": f"chatroom:{actor}", "kind": "chatroom", "title": actor,
                 "section": "conversation", "unread": True}]

    def pending(actor):
        assert context.get() == actor
        both_reads[actor].wait(timeout=5)
        return {"requests": [{**request(), "key": actor}]}

    monkeypatch.setattr(web_isa, "_inbox_entries", items)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(list_user_pending_requests=pending)))

    def read(actor):
        token = context.set(actor)
        timings = {}
        try:
            with capture_timings(timings):
                value = mailbox[2](user_id=actor)
            assert set(timings) == {"portal_mailbox_items", "portal_mailbox_pending"}
            assert value["items"][0]["key"] == f"chatroom:{actor}"
            assert value["pending_requests"][0]["key"] == actor
        finally:
            context.reset(token)

    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(read, actor) for actor in both_reads]
        for future in futures:
            future.result(timeout=10)


def test_item_failure_joins_started_question_read_without_returning_partial_mailbox(mailbox, monkeypatch):
    started, release, finished, items_failed = Event(), Event(), Event(), Event()

    def pending(actor):
        started.set()
        assert release.wait(timeout=5)
        finished.set()
        return {"requests": [request()]}

    def unavailable(*_):
        assert started.wait(timeout=5)
        items_failed.set()
        raise RuntimeError("Items unavailable")

    monkeypatch.setattr(web_isa, "_inbox_entries", unavailable)
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(list_user_pending_requests=pending)))
    with ThreadPoolExecutor(max_workers=1) as caller:
        value = caller.submit(mailbox[2])
        try:
            assert items_failed.wait(timeout=5)
            with pytest.raises(TimeoutError):
                value.result(timeout=0.05)
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="Items unavailable"):
            value.result(timeout=5)
    assert finished.is_set()


def test_item_failure_cancels_queued_question_read_without_waiting_for_other_requests(mailbox, monkeypatch):
    release = Event()

    def unavailable(*_):
        raise RuntimeError("Items unavailable")

    monkeypatch.setattr(web_isa, "_inbox_entries", unavailable)
    with ThreadPoolExecutor(max_workers=1) as questions, ThreadPoolExecutor(max_workers=1) as caller:
        blocker = questions.submit(release.wait, 5)
        monkeypatch.setattr(web_isa, "_MAILBOX_QUESTION_READS", questions)
        value = caller.submit(mailbox[2])
        try:
            with pytest.raises(RuntimeError, match="Items unavailable"):
                value.result(timeout=1)
        finally:
            release.set()
        blocker.result(timeout=5)
    assert mailbox[1] == []

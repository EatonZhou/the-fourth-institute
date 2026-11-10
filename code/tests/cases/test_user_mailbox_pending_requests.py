"""User Mailbox projects the question owner's current answerable requests.

:module: workbench.chat
:importance: P1
:design: decision_15f7df737ed5
"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from management_portal import society_service, web_isa


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

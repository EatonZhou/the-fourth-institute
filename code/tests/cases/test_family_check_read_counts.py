"""Family mechanical checks avoid reads once an input is ineligible.

:module: lineage.team
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service import family_review, family_task_dispatches
from management_portal.society_service import resident_conversation_stream, resident_input_queue_domain
from management_portal.society_service.resident_context import work_summary


@pytest.mark.parametrize("state", ["queued", "running", "failed", "cancelled"])
def test_incomplete_input_needs_no_message_or_ref(monkeypatch, state):
    calls = []
    repo = SimpleNamespace(
        input_state=lambda *a: {"state": state},
        message=lambda *a: calls.append("message") or {"role": "user"},
        message_business_ref=lambda *a: calls.append("ref") or "",
    )
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repo)
    monkeypatch.setattr(work_summary, "_visible", lambda *a: {"family": {"role": "member"}})
    monkeypatch.setattr(work_summary, "_family", lambda *a: ({}, {"facade": ["facade"], "steward": ["steward"]}))
    assert work_summary.check_resident_work_summary("member", "member", "mid") == {"status": "skipped"}
    assert family_review._completed_position({"members": ["member"]}, {
        "type": "resident.work_finished", "subject_resident_id": "member", "payload": {"message_id": "mid"},
    }) is None
    assert calls == []


def test_dispatch_envelope_and_business_ref_share_one_read(monkeypatch):
    from management_portal.society_service.resident_conversation_records import message_from_storage
    stored = resident_input_queue_domain.build_message(
        "user", "task", "facade", message_id="msg_d1000000000000000000000000000001")
    stored["business_ref"] = "duty/family-summary"
    calls = []
    def read(*args):
        calls.append("stored")
        return stored
    repo = SimpleNamespace(
        stored_message=read, message=lambda *a: message_from_storage(read()),
        message_business_ref=lambda *a: read()["business_ref"],
        validate_message_identity=lambda *a: read(),
    )
    original, candidate = family_task_dispatches._validate_original(
        repo, "target", "facade", stored["message_id"], "task", "duty/family-summary")
    assert original["message_id"] == candidate["message_id"] == stored["message_id"]
    # One envelope read and one authoritative identity recheck.
    assert len(calls) == 2

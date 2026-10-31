"""The global conversation list shows a Resident from its first accepted input (decision_15f7df737ed5).

A user who has just sent the first message to a Resident sees that Resident in
the Agent Inbox while the input still waits in the queue, before any reply.

:module: spec.design-governance
"""
from __future__ import annotations

from management_portal import agent_conversation_index_backend
from management_portal.society_service import resident_directory_domain, resident_execution_work
from tests.cases.isa_test_support import run
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


def test_a_queued_first_input_puts_the_resident_in_the_global_list(delivery_resident):
    """发给 Resident 的第一条消息仍在排队时，该 Resident 已出现在全局对话列表中"""
    resident_id = delivery_resident["resident_id"]
    delivered = run("DeliverMessage", {
        "resident_id": resident_id,
        "message": {"content_parts": [{"type": "text", "text": "第一条消息，还在排队"}]},
    }, actor="sample_admin").raise_for_error()
    job = resident_execution_work.read(delivery_resident)
    queued_ids = [job.get("user_message_id"), *[item.get("user_message_id") for item in resident_execution_work.normalized_queue(job)]]
    assert delivered["message_id"] in queued_ids

    activity = resident_directory_domain.list_resident_conversation_activity("sample_admin", page_size=20)
    assert [item["resident_id"] for item in activity["residents"]] == [resident_id]

    entries = agent_conversation_index_backend.build_entries_page("sample_admin", page_size=20)["entries"]
    entry = next(item for item in entries if item["key"] == f"resident:{resident_id}")
    assert entry["kind"] == "resident-main"
    # No reply exists yet, so nothing is unread; the entry still shows the waiting work.
    assert entry["completion_signature"] == ""
    assert entry["status"] in {"queued", "running"}

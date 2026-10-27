"""A message that waits while a Resident's first turn runs starts the next turn.

:module: resident.conversation
:importance: P1
:design: decision_b5d349190743
"""

from __future__ import annotations

from unittest import mock

from management_portal.society_service.resident_execution_work import bind_execution
from management_portal.society_service import (
    resident_backend,
    resident_execution_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_input_queue_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401 - fixture


def test_messages_queued_during_the_first_turn_run_after_it(resident_record):  # noqa: F811
    """首轮运行期间排队的消息在首轮结束后依次执行

    没有会话的 Resident（新 Fork、provider_api Resident）以 bootstrap_session Job 运行第一轮；这一轮运行期间到达的消息进入队列。验证首轮结束后共享收尾逻辑推进队列：每条消息按到达顺序各运行一轮并发出完成通知，最后 Mailbox 为空、Job 清空，没有消息停在 pending。
    """
    # A Resident without a session (a new Fork, a provider_api Resident) runs
    # its input as a bootstrap_session Job.  That branch used to end by
    # clearing the Job, which left the messages queued meanwhile in the
    # Mailbox as pending, never delivered (10-06 to 10-10).
    resident_id = str(resident_record["resident_id"])
    first = {
        'execution_id': "execution-" + "5" * 24, "bootstrap_session": True, "actor": "test-actor",
        "user_message_id": "message_first_turn", "message": "first", "raw_message": "first", "queue": [],
        **resident_execution_work.continuation_fields(resident_record),
    }
    bind_execution(first, execution_id="execution-" + "5" * 24,
                    now="2026-10-10T04:00:00Z")
    continuation_id = str(first["continuation_id"])
    resident_execution_work.write(resident_record, first)
    sent: list[str] = []
    waiting: list[str] = []

    def send(record, job, **_options):
        sent.append(str(job.get("user_message_id") or ""))
        if len(sent) == 1:
            for text in ("second", "third"):
                waiting.append(resident_intake_domain.persist_resident_input(
                    resident_id, "test-actor", text, activation_policy="enqueue_and_trigger",
                )["message_id"])
            places = {row["message_id"]: row["place"] for row in get_lineage_data_store().mailbox_entries([resident_id])}
            assert places == {message_id: "queue" for message_id in waiting}
        reply = {"role": "agent", 'execution_id': job['execution_id'], "created_at": "2026-10-10T05:00:00Z",
                 "content_parts": [{"type": "text", "text": "done"}]}
        return reply, dict(record, session_state="active")

    with mock.patch.object(resident_backend, "record_session_id", return_value=""), \
            mock.patch.object(resident_execution_domain, "send_to_resident_backend", side_effect=send), \
            mock.patch.object(resident_execution_domain, "_notify_lineage_message_completed") as notify, \
            mock.patch.object(resident_input_queue_domain, "acknowledge_execution", return_value=True):
        resident_execution_domain.run_resident_execution(resident_id, continuation_id)

    assert sent == ["message_first_turn", *waiting]
    assert [call.args[2] for call in notify.call_args_list] == sent
    assert get_lineage_data_store().mailbox_entries([resident_id]) == []
    assert resident_execution_work.read(resident_record) == {}

"""GetInputState tells whether a message reaches, or is still in, the Resident's context (decision_2a3ff353165c).

The Family Router keeps its session (CASE-A 10-12) and sends its brief only while
the message that carried it has left the Router's context.

:module: resident.session
"""

from __future__ import annotations

from management_portal.society_service import execution_client, resident_conversation_stream
from management_portal.society_service import resident_workspace_domain
from tests.cases.test_cross_owner_storage_integration import owners  # noqa: F401 - fixture
from tests.cases.test_resident_mailbox import _queue
from tests.cases.test_wall04_residual_20260919 import instruction, team  # noqa: F401 - fixture


def _state(team, resident_id: str, message_id: str, **options) -> dict:
    return instruction(team, "GetInputState", {"resident_id": resident_id, "message_id": message_id, **options}).result[
        "input_state"]


def test_a_waiting_message_will_reach_the_context_and_a_finished_one_asks_the_trace(team, monkeypatch):
    """GetInputState 说明消息会不会进入或是否仍在 Resident 的上下文中

    include_context 时，等待中的消息 in_context 为真；已处理的消息按 Resident 最新的上下文判断，并带上上下文清空时间。
    """
    resident_id, (_head, a, b, c) = _queue(team)
    asked = []

    def in_context(agent_id, message_id, *, since=""):
        asked.append((agent_id, message_id, since))
        return message_id == a

    monkeypatch.setattr(execution_client, "message_in_context", in_context)

    assert "in_context" not in _state(team, resident_id, b)
    assert _state(team, resident_id, b, include_context=True)["in_context"] is True and asked == []

    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(resident_id, a, "completed")
    repository.set_input_state(resident_id, c, "failed")
    since = str(resident_workspace_domain._find_resident(resident_id).get("context_reset_at") or "")
    assert _state(team, resident_id, a, include_context=True)["in_context"] is True
    assert _state(team, resident_id, c, include_context=True)["in_context"] is False
    assert asked == [(resident_id, a, since), (resident_id, c, since)]


def test_a_mailbox_group_member_went_into_the_context_as_its_group(team, monkeypatch):
    """Mailbox 群体成员按所在群体判断是否在上下文中

    合并进群体的消息，in_context 按群体消息判断。
    """
    resident_id, (_head, a, b, _c) = _queue(team)
    group_id = instruction(team, "MergeMailboxItems", {"resident_id": resident_id, "message_ids": [a, b]}).result[
        "group_message_id"]
    asked = []
    monkeypatch.setattr(execution_client, "message_in_context",
                        lambda agent_id, message_id, *, since="": asked.append(message_id) or True)

    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(resident_id, group_id, "completed")
    member = _state(team, resident_id, a, include_context=True)
    assert member["grouped_into_message_id"] == group_id and member["in_context"] is True
    assert asked == [group_id]

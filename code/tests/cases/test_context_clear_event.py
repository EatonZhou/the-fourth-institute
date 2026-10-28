"""
:module: resident.conversation
:importance: P1
:aspects: functional, behavior
:design: decision_c524c775e0be
"""
import pytest

from tests.cases.test_resident_conversation_boundaries import repository, message
from management_portal.society_service import resident_lifecycle_steps_domain
from management_portal.society_service.resident_conversation_stream import CONTEXT_CLEARED, MESSAGE_UPDATED


@pytest.mark.parametrize("old_count", [0, 1])
def test_completed_clear_projects_inheritance_after_archive_and_preserves_new_input(repository, monkeypatch, old_count):
    """Reset 归档完成事件与移除投影原子提交

    清理完成事件覆盖空会话与旧消息会话；投影失败原子回滚，恢复保留边界后的新输入。

    :design-point:
    """
    resident_id = "resident_111111111111"
    if old_count:
        repository.commit_user_message(resident_id, message(1))
    step = {
        "kind": "context_archive", "resident_id": resident_id,
        "message_ids": repository.context_reset_boundary(resident_id),
        "trace_position": repository.agent_data.trace_length(resident_id),
        "archived_at": "2026-10-12T19:00:00Z", "reset_at": "2026-10-12T19:00:00Z",
    }
    repository.commit_user_message(resident_id, message(2))
    before = repository.snapshot(resident_id)["cursor"]
    original = repository.events.record_event

    def fail_completion(*args, **kwargs):
        if args[2] == CONTEXT_CLEARED:
            raise RuntimeError("clear event commit failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository.events, "record_event", fail_completion)
    with pytest.raises(RuntimeError, match="clear event commit failed"):
        resident_lifecycle_steps_domain.apply_resident_lifecycle_step("clear:0", step)
    # Removal and completed-clear projection must roll back together. Recovery
    # uses the durable cutoff, never clearing input which arrived after it.
    assert repository.events_after(resident_id, before)["events"] == []
    monkeypatch.setattr(repository.events, "record_event", original)
    resident_lifecycle_steps_domain.apply_resident_lifecycle_step("clear:0", step)
    events = repository.events_after(resident_id, before)["events"]
    assert [event["event_type"] for event in events] == [MESSAGE_UPDATED] * old_count + [CONTEXT_CLEARED]
    assert events[-1]["payload"] == {"context_cleared": True}
    assert repository.context_reset_boundary(resident_id) == [message(2)["message_id"]]

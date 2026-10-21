from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_state, resident_protocol


def test_intake_state_contract_is_canonical() -> None:
    """intake 状态只有 open 和 closed，open 与 close 转换分别得到这两个状态，其他转换被拒绝"""
    assert resident_state.INTAKE_DOMAIN == "intake"
    assert resident_state.INTAKE_FIELD == "intake_state"
    assert resident_state.INTAKE_VALUES == {"open", "closed"}
    assert resident_state.INTAKE_TRANSITIONS == {"open", "close"}
    assert resident_state.INTAKE_DEFAULT == "open"
    assert resident_state.intake_state_for_transition("open") == "open"
    assert resident_state.intake_state_for_transition("close") == "closed"
    with pytest.raises(ValueError, match="open 或 close"):
        resident_state.intake_state_for_transition("pause")


def test_conversation_controls_belong_to_society_service(monkeypatch, subtests, isolated_portal_case):
    """对话控制按管理权限决定能否发送，有进行中的输入才能停止，intake 关闭时给出发送提示"""
    for allowed in [True, False]:
        for intake in ["open", "closed"]:
            for active_message_id in ["", "active-input"]:
                with subtests.test(allowed=allowed, intake=intake, active_message_id=active_message_id), isolated_portal_case() as case_path:
                    with monkeypatch.context() as monkeypatch:
                        target = SimpleNamespace(record={"resident_id": "resident_111111111111", "backend": "codex"})
                        monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda record, actor: allowed)
                        state = {"intake_state": intake, "active_message_id": active_message_id}
                        controls = resident_protocol._conversation_controls(target, "CASE-A", state)
                        assert controls["can_send"] is allowed
                        assert controls["can_stop"] is (allowed and bool(active_message_id))
                        assert bool(controls["disabled_reason"]) is not allowed
                        assert bool(controls["send_hint"]) is (intake == "closed")
                        assert "controls" not in state


def test_native_command_acknowledgement_contains_actor_controls(monkeypatch, subtests, isolated_portal_case):
    """投递输入和清空队列的回执附带调用者的对话控制，不改动原响应"""
    for allowed in [True, False]:
        for operation in ["deliver", "queue"]:
            with subtests.test(allowed=allowed, operation=operation), isolated_portal_case() as case_path:
                with monkeypatch.context() as monkeypatch:
                    resident_id = "resident_111111111111"
                    state = {"work_state": "running", "intake_state": "closed", "active_message_id": "input"}
                    response = {"resident_id": resident_id, "state": state, "cursor": 8}
                    monkeypatch.setattr(resident_protocol.resident_access, "can_manage_resident", lambda record, actor: allowed)
                    monkeypatch.setattr(resident_protocol.resident_workspace_domain, "resolve_resident_runtime_target", lambda *args, **kwargs: SimpleNamespace(resident_id=resident_id, record={"resident_id": resident_id, "backend": "codex"}))
                    if operation == "deliver":
                        monkeypatch.setattr(resident_protocol.resident_execution_domain, "deliver_resident_message_content", lambda *args, **kwargs: response)
                        result = resident_protocol.deliver_resident_input({
                            "resident_id": resident_id,
                            "message": {"message_id": "msg_11111111111111111111111111111111", "role": "user", "content_parts": [{"type": "text", "text": "queue this"}]},
                        }, "CASE-A")
                    else:
                        monkeypatch.setattr(resident_protocol.resident_intake_domain, "clear_resident_queued_inputs", lambda *args, **kwargs: response)
                        result = resident_protocol.clear_resident_queue(resident_id, "CASE-A")
                    assert result["cursor"] == 8
                    assert result["controls"]["can_send"] is allowed
                    assert result["controls"]["can_stop"] is allowed
                    assert "controls" not in response


def test_intake_transition_acknowledgement_remains_a_transition_summary(monkeypatch, subtests, isolated_portal_case):
    """intake 状态转换的回执只含转换前后状态，不附带对话控制"""
    for transition, previous_state, state in [
    ("open", "closed", "open"),
    ("close", "open", "closed"),
]:
        with subtests.test(transition=transition, previous_state=previous_state, state=state), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                resident_id = "resident_111111111111"
                response = {
                    "resident_id": resident_id,
                    "state_domain": "intake",
                    "field": "intake_state",
                    "previous_state": previous_state,
                    "state": state,
                    "intake_state": state,
                    "changed": True,
                }
                monkeypatch.setattr(
                    resident_protocol.resident_intake_domain,
                    "transition_resident_state",
                    lambda *args, **kwargs: response,
                )
                monkeypatch.setattr(
                    resident_protocol,
                    "_conversation_controls",
                    lambda *args, **kwargs: pytest.fail("A transition summary is not a Resident state snapshot"),
                )
                result = resident_protocol.change_resident_state(
                    resident_id, "CASE-A", state_domain="intake", transition=transition,
                )
                assert result == response
                assert "controls" not in result

"""G2 Resident, settings, workspace and queue public result regressions.

:module: spec.design-governance
"""
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.hatg.kernel import Invocation
from management_portal.society_service import (
    lineage_branch_transitions,
    lineage_domain,
    lineage_workspace_binding,
    resident_domain,
    resident_domain_common,
    resident_intake_domain,
    resident_protocol,
    resident_workspace_domain,
)
from management_portal.society_service.isa import execution, residents
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401
from tests.cases.test_session_restart import _restart_domain
from management_portal.execution import slash_commands


def invocation(name, **arguments):
    return Invocation(
        declaration=catalog.instruction_catalog()[name],
        call=InstructionCall.from_payload({"instruction": name, "arguments": arguments}),
        context=InstructionContext(caller="CASE-A", entry="web"),
        instruction_id="inst_lin_g2audit",
    )


def test_restart_read_reports_the_actual_restart(monkeypatch):
    """GetSessionRestart 返回实际受理的那次会话重启及其源和目标会话"""
    owned_attributes = set(vars(execution_port))
    try:
        with monkeypatch.context() as patch:
            record, _operations, _saved = _restart_domain(patch)
            accepted = resident_domain.restart_resident_session(record["resident_id"], "alice", {})
            result = residents.get_session_restart(invocation("GetSessionRestart", resident_id=record["resident_id"]))
            assert result["restart_id"] == accepted["restart_id"]
            assert result["status"] == "accepted"
            assert result["source_session"] == accepted["source"]
            assert result["target_session"] == accepted["target"]
    finally:
        # monkeypatch restores __getattr__ methods as instance attributes;
        # remove those shadows so later module-level mocks still reach the port.
        for name in set(vars(execution_port)) - owned_attributes:
            delattr(execution_port, name)


@pytest.mark.parametrize("mode", ["full", "compacted", "recent-5", "recent-10"])
def test_fork_rejects_a_different_context_source_outside_message_mode(mode):
    """非 message 模式的 Fork 拒绝指定其他 Resident 作为 context 来源"""
    with pytest.raises(ValueError, match="context_source_resident_id"):
        lineage_branch_transitions.create_derived_resident(
            "resident_aaaaaaaaaaaa",
            {"mode": mode, "context_source_resident_id": "resident_bbbbbbbbbbbb"},
            "CASE-A", "lineage_000000000001", resident_authority=mock.Mock(),
        )


@pytest.mark.parametrize("own", [False, True])
def test_bind_workspace_returns_the_selected_root_and_is_repeatable(tmp_path, monkeypatch, own):
    """BindWorkspace 返回所选的 Workspace 根，重复绑定得到相同结果"""
    resident_id = "resident_aaaaaaaaaaaa"
    lineage_id = "lineage_000000000001"
    path = str(tmp_path)
    root = {"workspace_id": "workspace_222222222222", "path": path, "is_owned": own}
    workspace = {"workspace_id": "workspace_111111111111", "roots": [root], "workspaces": [root]}
    def bind(_lineage, payload, actor):
        mounted = lineage_workspace_binding.mount_reference(
            lineage_id=lineage_id, node={"id": resident_id}, lineage_nodes=[{"id": resident_id}],
            node_resident_id=resident_id, actor=actor, workspace_path=payload["path"],
            workspace_id=root["workspace_id"],
            mount=lambda *_a, **_kw: {"added": False, "path": path}, workspaces=lambda *_: workspace,
            node_resident=lambda node: node["id"],
        )
        return {"workspace": mounted["workspace"]}
    monkeypatch.setattr(residents, "domain", lambda: SimpleNamespace(bind_node_workspace=bind))
    monkeypatch.setattr(residents, "lineage_of_resident", lambda _: lineage_id)
    inv = invocation("BindWorkspace", resident_id=resident_id, path=path)
    first = residents.bind_workspace(inv)["binding"]
    again = residents.bind_workspace(inv)["binding"]
    assert first["path"] == path
    assert first["workspace_id"]
    assert again == first
    if not own:
        assert first["workspace_id"] != workspace["workspace_id"]


def test_unchanged_settings_keep_the_same_version(resident_record, monkeypatch):
    """提交与当前相同的 Resident 设置不改变设置版本"""
    resident_id = resident_record["resident_id"]
    inv = invocation("UpdateResidentSettings", resident_id=resident_id, changes={"collaboration_mode": "plan"})
    residents.update_resident_settings(inv)
    before = residents.get_resident_settings(invocation("GetResidentSettings", resident_id=resident_id))
    monkeypatch.setattr(resident_domain_common, "now_iso", lambda: "2099-01-01T00:00:00Z")
    residents.update_resident_settings(inv)
    assert residents.get_resident_settings(invocation("GetResidentSettings", resident_id=resident_id)) == before


def _cancel_with(monkeypatch, cancel, states):
    """Fake the public input command; states are its resulting stored states."""
    seen = list(states)

    def stored(*_):
        state = seen.pop(0) if len(seen) > 1 else seen[0]
        if isinstance(state, Exception):
            raise state
        return {"state": state}

    monkeypatch.setattr(execution, "lineage_of_resident", lambda _: "lineage_000000000001")
    monkeypatch.setattr(execution, "domain", lambda: SimpleNamespace(
        cancel_lineage_resident_execution=cancel,
    ))
    monkeypatch.setattr(execution, "protocol", lambda: SimpleNamespace(_stored_resident_input_state=stored))
    return execution.cancel_input(invocation("CancelInput", resident_id="resident_aaaaaaaaaaaa", message_id="msg_one"))


def _gone(*_):
    raise KeyError("queue item not found")


def test_cancel_queue_receipt_contains_cancelled_input(monkeypatch):
    """CancelInput 撤回等待中的消息，回执包含被取消输入的状态"""
    result = _cancel_with(monkeypatch, lambda *_: {"cancelled": True}, ["cancelled"])
    assert result["input_state"]["state"] == "cancelled"


def test_cancelling_a_finished_message_keeps_its_result(monkeypatch):
    """取消已结束的消息不报错，保留原结果并在回执中给出其状态

    消息在取消途中结束时回执带 already_terminal；公共取消合同报告已结束消息原样未变。
    """
    result = _cancel_with(monkeypatch, _gone, ["completed"])
    assert result["already_terminal"] is True
    assert result["input_state"]["state"] == "completed"
    stopped = _cancel_with(monkeypatch, lambda *_: {"already_terminal": True}, ["failed"])
    assert stopped["already_terminal"] is True and stopped["input_state"]["state"] == "failed"


def test_cancelling_an_unknown_message_still_reports_it_missing(monkeypatch):
    """取消不存在的消息仍报告消息不存在"""
    with pytest.raises(KeyError):
        _cancel_with(monkeypatch, _gone, [KeyError("msg_one")])


def test_cancel_receipt_preserves_running_state_until_cancellation_is_confirmed(monkeypatch):
    """公共合同接受停止请求后，ISA 保留尚未确认结束的运行状态"""
    receipt = {"resident_id": "resident_aaaaaaaaaaaa", "message_id": "msg_one",
               "cancellation_requested": True, "cancelled": False}

    result = _cancel_with(monkeypatch, lambda *_: receipt, ["running"])
    assert result["cancellation_requested"] is True
    assert result["input_state"]["state"] == "running"


@pytest.mark.parametrize("receipt,status", [({"injected": True}, "delivered"), ({"delivery_pending": True}, "pending"), ({"scheduled_next_turn": True}, "next_turn")])
def test_inject_receipt_reports_delivery_outcome(monkeypatch, receipt, status):
    """InjectMailboxItem 的回执报告已注入、待投递或下一轮执行"""
    monkeypatch.setattr(execution, "lineage_of_resident", lambda _: "lineage_000000000001")
    monkeypatch.setattr(execution, "domain", lambda: SimpleNamespace(inject_lineage_resident_queue_item_into_active_turn=lambda *_: receipt))
    result = execution.inject_mailbox_item(invocation("InjectMailboxItem", resident_id="resident_aaaaaaaaaaaa", message_id="msg_one"))
    assert result["status"] == status


def test_quiesce_clears_closed_intake_waiting_inputs(resident_record, monkeypatch):
    """静默 Resident 时清除入口关闭期间等待的输入并标为已取消"""
    from management_portal.society_service import resident_registration_domain, resident_record_domain, resident_session_backend_domain
    resident_id = resident_record["resident_id"]
    resident_protocol.change_resident_state(resident_id, "CASE-A", state_domain="intake", transition="close")
    monkeypatch.setattr(resident_session_backend_domain, "handle_resident_native_command", lambda *_a, **_kw: {})
    monkeypatch.setattr(slash_commands, "handle_slash_command", lambda *_a, **_kw: {"handled": False})
    monkeypatch.setattr(resident_record_domain, "resolve_context_refs", lambda *_a, **_kw: {})
    monkeypatch.setattr(resident_registration_domain, "build_resident_prompt", lambda *_a, **_kw: "held prompt")
    delivered = [resident_domain.deliver_resident_message_content(resident_id, "CASE-A", content) for content in ("one", "two")]
    assert resident_protocol.get_resident_state(resident_id, "CASE-A")["state"]["active_message_id"] == ""
    result = lineage_domain.quiesce_lineage_resident(resident_record["lineage_id"], {"target": resident_id}, "CASE-A")
    assert result["cleared_queue_count"] == 2
    assert [item for item in resident_protocol.get_resident_mailbox(resident_id, "CASE-A")["pending"] if item.get("kind") != "info"] == []
    assert [resident_protocol.get_resident_input_state(resident_id, "CASE-A", item["message_id"])["state"] for item in delivered] == ["cancelled", "cancelled"]

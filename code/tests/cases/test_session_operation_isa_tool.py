"""A Resident session started by a restart or account switch has the HATG ISA tool.

The full TAU rerun moved module executors to another account; their new
Codex threads were started by a session operation whose request did not name
the Resident, so the ISA Harness tool (attached only to Resident executions)
was left out, and a Codex thread keeps the tools it started with: the
executor had no hatg_instruction for the rest of its life.

:module: resident.session
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from management_portal.execution.harness_isa_tool import _resident_caller
from management_portal.society_service.resident_session_backend_domain import (
    build_backend_session_operation_request as _build_backend_session_operation_request,
)


def _request(binding_id: str, operation: str = "turn"):
    return _build_backend_session_operation_request(
        backend="codex", operation=operation, actor="sample_admin", cwd=Path("/tmp"),
        binding_id=binding_id, correlation_id="restart_0123456789abcdef", prompt="handoff",
    )


def test_a_session_operation_for_a_resident_binding_gets_the_isa_tool():
    """重启或切换账号建立的 Resident 新会话带有 HATG ISA Harness 工具"""
    request = _request("resident:resident_626711e6e964")
    assert request.metadata["resident_id"] == "resident_626711e6e964"
    # The ISA Harness tool is attached to an execution whose caller is a Resident.
    assert _resident_caller(SimpleNamespace(request=request)) == "resident_626711e6e964"


def test_explicit_metadata_and_other_bindings_are_left_alone():
    """非 Resident 绑定的会话操作不被当作 Resident 执行"""
    assert "resident_id" not in _request("agent:background-0123456789ab").metadata


def test_a_restart_turn_is_recorded_in_the_residents_own_trace():
    """重启 Session 的初始化回合记在 Resident 自己的轨迹中

    重启 Session 时的初始化或交接回合以 Resident 身份运行，它的 Segment 进入该 Resident 的 agent_trace；没有模型回合的 Session 操作（释放、原生 Fork）仍用绑定名，不算 Resident 的最近 Session。
    """
    # The restart's initialization or handoff turn runs on a prepared binding;
    # it is still the Resident's work, so it runs as the Resident and its
    # Segments go to the Resident's agent trace.
    restart = _build_backend_session_operation_request(
        backend="codex", operation="turn", actor="sample_admin", cwd=Path("/tmp"),
        binding_id="session-restart:restart_0123456789abcdef", correlation_id="restart_0123456789abcdef",
        prompt="handoff", metadata={"resident_id": "resident_626711e6e964"},
    )
    assert restart.agent_id == "resident_626711e6e964"
    assert restart.identity.principal.id == "resident_626711e6e964"
    # An operation without a model turn keeps the binding's name, so it never
    # counts as the Resident's latest session.
    release = _request("resident:resident_626711e6e964", operation="release")
    assert release.agent_id == "resident:resident_626711e6e964"

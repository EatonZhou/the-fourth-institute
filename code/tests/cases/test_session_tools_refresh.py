"""Portal replaces a Codex session that can never offer hatg_instruction.

A Codex thread keeps the dynamic tools it started with; resume and fork
cannot add any.  The next input of such a Resident waits while Portal hands
the conversation to a new thread (compact handoff), then runs there.

:module: resident.session
:design: decision_d19b9909a674
"""
from __future__ import annotations

import time
from contextlib import nullcontext

import pytest

from management_portal.execution import harness_isa_tool
from management_portal.execution.session_tools import REQUIRED_SESSION_TOOLS, session_lacks_required_tools
from management_portal.society_service import (
    resident_backend,
    resident_directory_domain,
    resident_domain,
    resident_execution_domain,
    resident_intake_domain,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_restart_domain,
    resident_state,
    resident_workspace_domain,
)
from tests.cases.test_session_restart import _restart_domain

STALE = {"provider": "codex", "backend_session_id": "thread-old", "backend_route_id": "zero",
         "provider_session_ref": "ref-old", "harness_tool_names": ("jev_decide", "read_portal_resource")}
JOB = {'execution_id': "execution-1", "actor": "bob", "user_message_id": "msg-1", "activation_policy": "enqueue_and_trigger"}


def test_required_tools_are_the_isa_tool_and_only_codex_sessions_keep_theirs():
    """只有缺少 hatg_instruction 的 Codex 会话需要替换

    Codex 会话缺少 hatg_instruction（含早于工具记录的空记录）时判定为需要替换；已有该工具、其他 Provider 或尚无会话时不替换。

    :design-point:
    """
    assert harness_isa_tool.ISA_TOOL_NAME in REQUIRED_SESSION_TOOLS
    assert session_lacks_required_tools(STALE)
    # Bindings recorded before tool names were kept are older than the tool.
    assert session_lacks_required_tools({**STALE, "harness_tool_names": ()})
    assert not session_lacks_required_tools({**STALE, "harness_tool_names": ("hatg_instruction",)})
    assert not session_lacks_required_tools({**STALE, "provider": "claude"})
    assert not session_lacks_required_tools({**STALE, "backend_session_id": ""})
    assert not session_lacks_required_tools(None)


@pytest.mark.parametrize(("job", "record", "binding", "due"), [
    (JOB, {}, STALE, True),
    (JOB, {}, {**STALE, "harness_tool_names": ("hatg_instruction",)}, False),
    # Only an input that has not reached Execution waits for a new session.
    ({**JOB, "submitted_at": "2026-10-06T00:00:00Z"}, {}, STALE, False),
    ({**JOB, 'operation': "backend_compact"}, {}, STALE, False),
    ({**JOB, resident_state.INTAKE_FIELD: resident_state.INTAKE_CLOSED}, {}, STALE, False),
    ({**JOB, "finished_at": "2026-10-06T00:00:00Z"}, {}, STALE, False),
    # One attempt per session ...
    (JOB, {"session_tools_refresh": {"source_generation": "SAME", "attempted_at": "2026-01-01T00:00:00Z"}}, STALE, False),
    # ... and at most one a day per Resident.
    (JOB, {"session_tools_refresh": {"source_generation": "other", "attempted_at": "NOW"}}, STALE, False),
    (JOB, {"session_tools_refresh": {"source_generation": "other", "attempted_at": "2026-01-01T00:00:00Z"}}, STALE, True),
])
def test_refresh_is_due_once_for_an_input_that_has_not_started(job, record, binding, due):
    """会话工具替换只为尚未进入执行的输入触发且有次数上限

    活动、未进入 Execution、非 /compact 的输入才触发替换；同一会话只尝试一次，同一 Resident 每天最多一次。

    :design-point:
    """
    record = {"resident_id": "resident_aaaaaaaaaaaa", **record}
    last = record.get("session_tools_refresh") or {}
    if last.get("source_generation") == "SAME":
        last["source_generation"] = resident_restart_domain._session_restart_source(record, binding)["generation"]
    if last.get("attempted_at") == "NOW":
        last["attempted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    assert resident_restart_domain.session_tools_refresh_due(record, job, binding) is due


def test_smart_restart_of_a_session_without_the_tool_hands_over_compactly(monkeypatch, isolated_portal_case):
    """智能重启遇到缺工具的会话改用精简交接

    smart 策略在同 Provider 同账号时本应原生 Fork；来源会话缺少 hatg_instruction 时改选 compact，新线程带上当前工具。

    :design-point:
    """
    with isolated_portal_case():
        record, operations, _saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(resident_backend, "session_record", lambda *_args, **_kwargs: {
            **STALE, "binding_id": "resident:resident_aaaaaaaaaaaa"})
        result = resident_domain.restart_resident_session(
            record["resident_id"], "alice", {"target_provider": "codex", "context_strategy": "smart"})
        assert result["context_strategy"] == "compact"
        assert operations[0]["operation"] == "turn"


def test_portal_restarts_for_the_waiting_input_without_owner_rights(monkeypatch, isolated_portal_case):
    """Portal 为等待中的输入替换会话，不需要 owner 权限

    非 owner 直接调用 RestartSession 被拒绝；Portal 以等待消息的发送者提交 compact 重启，记录 reason=harness_tools 与 restart_id。

    :design-point:
    """
    with isolated_portal_case():
        record, operations, _saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(resident_backend, "session_record", lambda *_args, **_kwargs: {
            **STALE, "binding_id": "resident:resident_aaaaaaaaaaaa"})
        monkeypatch.setattr(resident_directory_domain, "_can_manage", lambda *_args: False)
        with pytest.raises(PermissionError):
            resident_domain.restart_resident_session(record["resident_id"], "bob", {"context_strategy": "compact"})

        record["session_tools_refresh"] = {"source_generation": "g", "attempted_at": "2026-10-06T00:00:00Z"}
        record["intake_transition_id"] = "t-1"
        state = resident_restart_domain.refresh_resident_session_tools(record["resident_id"], JOB, "t-1")
        assert state["status"] == "accepted"
        assert state["reason"] == resident_restart_domain.SESSION_TOOLS_REFRESH_REASON
        assert state["context_strategy"] == "compact"
        assert state["submitted_by"] == "bob"
        assert operations[0]["operation"] == "turn" and operations[0]["actor"] == "bob"
        assert record["session_tools_refresh"]["restart_id"] == state["restart_id"]
        assert operations[0]["step"]["intake_transition_id"] == "t-1"
        from management_portal.society_service import resident_conversation_stream
        monkeypatch.setattr(resident_conversation_stream.get_repository(), "set_state", lambda *_: None)
        resident_restart_domain._resume_resident_session_restart(operations[0]["step"])
        assert record["intake_open"] is True


@pytest.mark.parametrize("own_transition", [True, False])
def test_a_refused_refresh_reopens_intake_and_the_input_runs_on_the_old_session(monkeypatch, own_transition):
    """会话工具替换被拒绝时重新打开接单

    重启被拒绝时记录错误；接单仍是本次关闭的那次转换时重新打开，否则只保存记录，输入随后在原会话执行。

    :design-point:
    """
    record = {"resident_id": "resident_aaaaaaaaaaaa", "intake_transition_id": "t-1" if own_transition else "t-2",
              "session_tools_refresh": {"source_generation": "g", "attempted_at": "2026-10-06T00:00:00Z"}}
    opened, saved = [], []
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("quota reserved")))
    monkeypatch.setattr(resident_lifecycle_domain, "_state_lock", nullcontext)
    monkeypatch.setattr(resident_workspace_domain, "_find_resident", lambda *_args: record)
    monkeypatch.setattr(resident_workspace_domain, "_save_resident_record", saved.append)
    monkeypatch.setattr(resident_intake_domain, "_commit_resident_intake_state",
                        lambda current, state: opened.append((dict(current), state)))

    assert resident_restart_domain.refresh_resident_session_tools(record["resident_id"], JOB, "t-1") is None
    written = opened[0][0] if own_transition else saved[0]
    assert written["session_tools_refresh"] == {
        "source_generation": "g", "attempted_at": "2026-10-06T00:00:00Z", "error": "quota reserved"}
    # Intake reopens only when nobody changed it since the refresh closed it.
    assert [state for _record, state in opened] == ([resident_state.INTAKE_OPEN] if own_transition else [])


def test_the_next_input_waits_while_the_session_is_replaced(monkeypatch):
    """下一条输入在会话替换期间等待

    缺工具的 Codex 会话：恢复执行时先关闭接单并发起替换，不启动输入；替换被拒绝时同一次调用在原会话启动输入；会话已有工具时立即启动。

    :design-point:
    """
    record = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex", "registration_state": "ready"}
    job = dict(JOB)
    calls = []
    monkeypatch.setattr(resident_lifecycle_domain, "_state_lock", nullcontext)
    monkeypatch.setattr(resident_lifecycle_domain, "_lifecycle_operations",
                        lambda: type("Ops", (), {"pending": staticmethod(lambda *_: False)})())
    monkeypatch.setattr(resident_execution_domain, "_resident_record_for_execution", lambda *_args: record)
    monkeypatch.setattr(resident_execution_domain, "_active_resident_continuations", lambda: 0)
    monkeypatch.setattr(resident_execution_work, "read", lambda *_args: job)
    monkeypatch.setattr(resident_execution_work, "continuation_needed", lambda *_args: True)
    monkeypatch.setattr(resident_backend, "session_record", lambda *_args, **_kwargs: STALE)
    monkeypatch.setattr(resident_intake_domain, "_commit_resident_intake_state",
                        lambda current, state: current.update(intake_transition_id="t-1") or calls.append(state))
    monkeypatch.setattr(resident_execution_domain, "_prepare_resident_continuation",
                        lambda *_args, **_kwargs: calls.append("prepared") or None)
    monkeypatch.setattr(resident_restart_domain, "refresh_resident_session_tools",
                        lambda resident_id, held, transition: calls.append(("refresh", held['execution_id'], transition))
                        or {"status": "accepted"})

    resident_execution_domain.resume_resident_execution(record["resident_id"])
    assert calls == [resident_state.INTAKE_CLOSED, ("refresh", "execution-1", "t-1")]
    assert record["session_tools_refresh"]["source_generation"]

    # Refused: the same call starts the input on the old session.
    calls.clear()
    record.pop("session_tools_refresh")
    monkeypatch.setattr(resident_restart_domain, "refresh_resident_session_tools",
                        lambda *_args: calls.append("refused") or None)
    resident_execution_domain.resume_resident_execution(record["resident_id"])
    assert calls == [resident_state.INTAKE_CLOSED, "refused", "prepared"]

    # A session that offers the tool starts the input at once.
    calls.clear()
    record.pop("session_tools_refresh")
    monkeypatch.setattr(resident_backend, "session_record",
                        lambda *_args, **_kwargs: {**STALE, "harness_tool_names": ("hatg_instruction",)})
    resident_execution_domain.resume_resident_execution(record["resident_id"])
    assert calls == ["prepared"]

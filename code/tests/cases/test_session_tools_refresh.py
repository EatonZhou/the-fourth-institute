"""Ordinary inputs retain their session regardless of tool-registration metadata.

:module: resident.session
:design: decision_c47a13e8b962
"""
from __future__ import annotations

from contextlib import nullcontext
import copy

import pytest

from management_portal.execution import harness_isa_tool
from management_portal.execution.session_tools import REQUIRED_SESSION_TOOLS, session_lacks_required_tools
from management_portal.society_service import (
    family_quota, resident_backend, resident_delivery_work, resident_domain,
    resident_execution_domain, resident_intake_domain, resident_execution_work,
    resident_lifecycle_domain, resident_restart_domain, resident_state,
    resident_workspace_domain,
)
from tests.cases.test_session_restart import _restart_domain

STALE = {"provider": "codex", "backend_session_id": "thread-old", "backend_route_id": "zero",
         "provider_session_ref": "ref-old", "harness_tool_names": ("jev_decide", "read_portal_resource")}
JOB = {"execution_id": "execution-1", "actor": "bob", "user_message_id": "msg-1", "activation_policy": "enqueue_and_trigger"}


def test_required_tools_are_the_isa_tool_and_only_codex_sessions_keep_theirs():
    """显式重启仍按来源会话的工具记录选择策略

    缺失工具名称的判定仅供显式重启策略使用，不代表允许自动替换会话。

    :design-point:
    """
    assert harness_isa_tool.ISA_TOOL_NAME in REQUIRED_SESSION_TOOLS
    assert session_lacks_required_tools(STALE)
    # An empty registration remains an input to the explicit restart policy.
    assert session_lacks_required_tools({**STALE, "harness_tool_names": ()})
    assert not session_lacks_required_tools({**STALE, "harness_tool_names": ("hatg_instruction",)})
    assert not session_lacks_required_tools({**STALE, "provider": "claude"})
    assert not session_lacks_required_tools({**STALE, "backend_session_id": ""})
    assert not session_lacks_required_tools(None)


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

@pytest.mark.parametrize("names", [None, (), ("jev_decide",), ("hatg_instruction",)])
def test_next_input_keeps_original_session_without_automatic_restart(monkeypatch, names):
    """空、缺失或不完整工具记录均不关闭接单、不重建；原输入沿原会话执行。"""
    record = {"resident_id": "resident_aaaaaaaaaaaa", "backend": "codex",
              "registration_state": "ready", "intake_open": True}
    binding = {**STALE, "binding_id": "resident:resident_aaaaaaaaaaaa"}
    if names is None:
        binding.pop("harness_tool_names")
    else:
        binding["harness_tool_names"] = names
    original_record, original_binding = copy.deepcopy(record), copy.deepcopy(binding)
    job = dict(JOB)
    calls = []
    monkeypatch.setattr(resident_lifecycle_domain, "resident_state_scope", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(resident_lifecycle_domain, "_state_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(resident_lifecycle_domain, "_lifecycle_operations",
                        lambda: type("Ops", (), {"pending": staticmethod(lambda *_: False)})())
    monkeypatch.setattr(resident_delivery_work, "drain", lambda *_a: None)
    monkeypatch.setattr(family_quota, "ensure_role_reserve", lambda *_a: False)
    monkeypatch.setattr(resident_execution_domain, "_resident_record_for_execution", lambda *_a: record)
    monkeypatch.setattr(resident_execution_domain, "_read_resident_execution_control", lambda *_a: job)
    monkeypatch.setattr(resident_execution_domain, "_active_resident_continuations", lambda: 0)
    monkeypatch.setattr(resident_execution_work, "read", lambda *_a: job)
    monkeypatch.setattr(resident_execution_work, "continuation_needed", lambda *_a: True)
    monkeypatch.setattr(resident_backend, "session_record", lambda *_a, **_k: binding)
    monkeypatch.setattr(resident_workspace_domain, "_find_resident", lambda *_a: record)
    monkeypatch.setattr(resident_workspace_domain, "_save_resident_record", lambda *_a: None)
    monkeypatch.setattr(resident_intake_domain, "_commit_resident_intake_state",
                        lambda current, state: current.update(intake_transition_id="t-1") or calls.append(state))
    monkeypatch.setattr(resident_restart_domain, "restart_resident_session",
                        lambda *_a, **_k: calls.append("restart") or {"status": "accepted", "restart_id": "unexpected"})

    def prepare(current, **kwargs):
        calls.append(("prepared", job["user_message_id"], resident_backend.session_record(current)["backend_session_id"]))
        return current["resident_id"], "continuation-1"

    monkeypatch.setattr(resident_execution_domain, "_prepare_resident_continuation", prepare)
    monkeypatch.setattr(resident_intake_domain, "_start_resident_continuation",
                        lambda current, key: calls.append(("started", current["resident_id"], key)))
    result = resident_execution_domain.resume_resident_execution(record["resident_id"])
    assert calls == [("prepared", "msg-1", "thread-old"),
                     ("started", record["resident_id"], "continuation-1")]
    assert result["residents"] == 1
    assert record == original_record
    assert binding == original_binding
    assert job == JOB

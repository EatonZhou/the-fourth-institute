"""Fork inherits source policy and trace with target Provider/account settings."""
from __future__ import annotations

import pytest

from management_portal.execution import resident_runtime_settings, codex_runtime_settings
from management_portal.society_service import (
    resident_catalog,
    resident_record_domain,
    resident_runtime_domain,
)
from management_portal.society_service import resident_domain as backend
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401 - fixture

INHERITED_KEYS = (
    "model", "reasoning_effort", "service_tier",
    "permission_preset", "sandbox", "permissions", "approval_policy",
    "prompt_injection", "allow_subagents", "agent_harness",
)


def test_fork_inherits_every_runtime_setting_of_the_source(resident_record) -> None:  # noqa: F811
    """Fork 继承来源会话的账号与运行设置

    Fork 出的 Resident 继承来源会话的全部运行设置，不只是模型。

    :module: resident.fork-handoff
    :importance: P1
    :design: decision_5bf5bfbd52cb, decision_b1ab45c4b3ec
    """
    from tests.cases.fork_context_test_support import record_session_turn

    source_settings = codex_runtime_settings.effective_codex_runtime_settings(
        resident_runtime_settings.mark_portal_manual_authority(
            resident_runtime_settings.merge_settings(
                resident_runtime_domain.read_resident_runtime_settings(resident_record),
                {
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "xhigh",
                    "service_tier": "priority",
                    "permission_preset": "full-access",
                    "prompt_injection": {"preset": "pure"},
                    "allow_subagents": True,
                },
                actor="CASE-A",
                updated_at="2026-09-30T10:00:00Z",
            ),
            actor="CASE-A",
            updated_at="2026-09-30T10:00:00Z",
        )
    )
    resident_runtime_domain.write_resident_runtime_settings(resident_record, source_settings)
    resident_record_domain.save_resident_record(resident_record)
    record_session_turn(resident_record)

    created = backend.create_resident_full_context_fork(
        str(resident_record["resident_id"]), "CASE-A", defer_backend_start=True,
    )
    child = resident_catalog.get_catalog().get(created["resident_id"])
    persisted = resident_runtime_domain.read_resident_runtime_settings(child)

    for key in INHERITED_KEYS:
        assert persisted.get(key) == source_settings.get(key), key
    assert persisted["permission_preset"] == "full-access"


def _full_access_settings() -> dict:
    return codex_runtime_settings.effective_codex_runtime_settings(
        resident_runtime_settings.merge_settings({}, {
            "backend": "claude",
            "model": "claude-opus-5-5",
            "reasoning_effort": "xhigh",
            "permission_preset": "full-access",
            "prompt_injection": {"preset": "full"},
            "allow_subagents": True,
        })
    )


def _inherit(*, authority: bool) -> tuple[dict, dict]:
    source_settings = _full_access_settings()
    # The target Lineage's own defaults differ from the source on purpose.
    target_defaults = resident_runtime_settings.merge_settings({}, {
        "backend": "claude", "model": "sonnet", "reasoning_effort": "medium",
        "permission_preset": "", "prompt_injection": {"preset": "standard"}, "allow_subagents": False,
    })
    inherited = resident_runtime_domain.fork_runtime_settings(
        source_settings, target_defaults,
        actor="CASE-A", updated_at="2026-09-30T10:00:00Z", workspace_authority=authority,
    )
    return inherited, source_settings


def test_fork_into_another_lineage_inherits_source_settings_not_target_defaults() -> None:
    """Fork 继承来源会话的账号与运行设置

    Fork 到另一个 Lineage 时，运行设置取自来源会话，而不是目标 Lineage 的默认值。

    :module: resident.fork-handoff
    :importance: P1
    :design: decision_5bf5bfbd52cb, decision_b1ab45c4b3ec
    """
    inherited, source_settings = _inherit(authority=True)

    for key in INHERITED_KEYS:
        assert inherited.get(key) == source_settings.get(key), key


def test_fork_without_workspace_authority_stays_read_only() -> None:
    """Fork 继承来源会话的账号与运行设置

    发起者对新 Resident 没有工作区权限时，权限类设置降为只读。

    :module: resident.fork-handoff
    :importance: P1
    :design: decision_5bf5bfbd52cb, decision_b1ab45c4b3ec
    """
    inherited, _source = _inherit(authority=False)

    assert inherited["model"] == "claude-opus-5-5"
    assert inherited["sandbox"] == "read-only"
    assert inherited["permission_preset"] == ""


@pytest.mark.parametrize("provider", ["claude", "kimi", "dsh", "opencode", "provider_api"])
@pytest.mark.parametrize("mode", ["full", "recent-5"])
def test_cross_provider_fork_keeps_trace_and_uses_target_account(resident_record, monkeypatch, provider, mode):
    """跨 Provider 分叉保留上下文轨迹并使用目标账号"""
    from management_portal.ai_provider_registry import AIProviderRegistry
    from management_portal.execution.accounts import default_execution_accounts
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.provider_catalog import AgentProvider
    from management_portal.society_service import resident_backend, resident_execution_work
    from tests.cases.fork_context_test_support import record_session_turn
    from tests.cases.model_api_test_support import register_accounts, register_apis

    register_apis({"id": "deepseek_primary", "provider": "deepseek",
                   "kind": "openai_compatible_chat", "enabled": True,
                   "default_model": "dsh-model", "models": [{"id": "dsh-model"}],
                   "routes": [{"backend": "dsh", "route_id": "deepseek"}]})

    monkeypatch.setattr(AIProviderRegistry, "entries", lambda self: [
        {"id": "anthropic", "provider": "anthropic", "kind": "claude_code_subscription", "enabled": True},
        {"id": "deepseek_primary", "provider": "deepseek", "kind": "openai_compatible_chat", "enabled": True,
         "default_model": "dsh-model", "models": [{"id": "dsh-model"}]},
    ])
    monkeypatch.setattr(AIProviderRegistry, "get", lambda self, route: next(
        (entry for entry in self.entries() if entry["id"] == route), None))
    monkeypatch.setattr(AgentProvider, "catalog", lambda self, route="": {
        "models": [f"{self.provider}-model"], "default_model": f"{self.provider}-model",
        "efforts_by_model": {}, "capabilities": {},
    })
    if provider == "opencode":
        # The isolated site seeds no OpenCode account.
        register_accounts({"account_id": "opencode_zen_free", "provider": "opencode", "kind": "service",
                           "route_id": "zen_free"})
    source_ids = record_session_turn(resident_record, question="Keep the cross-provider marker")
    account = (default_execution_accounts().get("deepseek_primary") if provider == "provider_api"
               else next(a for a in default_execution_accounts().accounts() if a.provider == provider))
    source_settings = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    args = {"provider": provider, "backend_route_id": account.account_id}
    if mode == "full":
        created = backend.create_resident_full_context_fork(resident_record["resident_id"], "CASE-A", **args)
    else:
        created = backend.create_resident_context_fork(resident_record["resident_id"], "CASE-A", 5, **args)
    child = resident_catalog.get_catalog().get(created["resident_id"])
    assert child["backend"] == provider
    assert child["backend_route_id"] == account.route_id
    assert resident_backend.execution_backend(child) == provider
    settings = resident_runtime_domain.read_resident_runtime_settings(child)
    assert settings["backend"] == provider
    assert settings["model"] == ("dsh-model" if provider == "provider_api" else f"{provider}-model")
    assert settings["reasoning_effort"] == ""
    assert resident_runtime_domain.read_resident_runtime_settings(resident_record) == source_settings
    snapshot = get_agent_data_store().agent_context_snapshot(child["resident_id"])
    assert [s["segment_id"] for s in snapshot["segments"]] == source_ids
    if child["session_state"] == "creating":
        assert resident_execution_work.read(child)["operation"] == "context_fork_create"

    if provider == "provider_api":
        from management_portal.society_service import resident_execution_domain as execution
        from management_portal.execution.contracts import SystemInstructions
        monkeypatch.setattr(execution, "_prepare_resident_prompt", lambda record, job: {
            **job, "actual_sent_prompt": job["raw_message"]})
        monkeypatch.setattr(execution, "resident_system_instructions", lambda *a: SystemInstructions())
        prepared = execution._portable_context_execution(child, {"actor": "CASE-A", "raw_message": "Continue"})
        request = execution._build_resident_execution_request(child, prepared)
        assert "Keep the cross-provider marker" in request.runtime_options["messages"][-1]["content"]
        assert request.runtime_options["messages"][-1]["content"].endswith("Continue")
        assert request.provider == account.route_id

    if mode == "full":
        returned = backend.create_resident_full_context_fork(
            child["resident_id"], "CASE-A", provider="codex", backend_route_id="zero", defer_backend_start=True,
        )
        codex_child = resident_catalog.get_catalog().get(returned["resident_id"])
        assert codex_child["backend"] == "codex"
        assert codex_child["backend_route_id"] == "zero"
        assert resident_runtime_domain.read_resident_runtime_settings(codex_child)["model"] != settings["model"]
        assert [s["segment_id"] for s in get_agent_data_store().agent_context_snapshot(codex_child["resident_id"])["segments"]] == source_ids


def test_model_api_fork_requires_an_account_when_switching_provider(resident_record):
    """切换到 Model API 创建分叉时必须选择账号"""
    from tests.cases.fork_context_test_support import record_session_turn

    record_session_turn(resident_record)
    with pytest.raises(ValueError, match="切换到 Model API 时必须选择账号"):
        backend.create_resident_full_context_fork(resident_record["resident_id"], "CASE-A", provider="provider_api")


def test_compacted_fork_checks_target_provider_before_creating(resident_record):
    """压缩分叉在创建前检查目标 Provider 支持能力"""
    from tests.cases.fork_context_test_support import record_session_turn

    record_session_turn(resident_record)
    with pytest.raises(ValueError, match="目标 Provider kimi 不支持压缩继承"):
        backend.create_resident_compacted_context_fork(resident_record["resident_id"], "CASE-A", provider="kimi")

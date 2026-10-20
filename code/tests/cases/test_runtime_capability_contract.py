from dataclasses import dataclass

import pytest

from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
from management_portal.execution.native_options import ClaudeOptions, CodexOptions, KimiOptions
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service.resident_execution_provider import capability_projection


@dataclass(frozen=True)
class _Request:
    runtime_options: dict


@dataclass(frozen=True)
class _Invocation:
    request: _Request


class _ClaudeAccount:
    provider = "claude"

    def accounts(self):
        return (ExecutionAccount("claude_test", "claude", "deepseek_primary", "test"),)

    def catalog(self, account):
        return {"models": ["test-model"], "default_model": "test-model",
                "efforts_by_model": {"test-model": ["low"]},
                "service_tiers_by_model": {"test-model": []}}


def test_resolved_catalog_and_resident_projection_share_adapter_capabilities():
    """账号运行目录和 Resident 投影给出与 Provider 适配器相同的运行能力，默认权限为 Full Access"""
    definition = AGENT_PROVIDERS["claude"]
    catalog = ExecutionAccounts((_ClaudeAccount(),)).runtime_catalog("claude_test")
    assert catalog["runtime_capabilities"] == definition.runtime_capabilities()
    assert catalog["runtime_capabilities"]["settings"] == catalog["runtime_settings"]
    assert catalog["runtime_capabilities"]["permission_presets"] == ["default", "full-access"]
    assert catalog["runtime_capabilities"]["default_permission_label"] == "Full Access"
    assert "service_tier" not in catalog["runtime_capabilities"]["settings"]
    assert "permission_policy" not in catalog

    codex = AGENT_PROVIDERS["codex"]
    projected = capability_projection({"backend": "codex", "mode": "new_session"})
    assert projected["runtime_capabilities"] == codex.runtime_capabilities()
    assert projected["runtime_capabilities"]["default_permission_label"] == "Full Access"


@pytest.mark.parametrize("provider", ["claude", "kimi"])
def test_permission_selection_is_validated_by_adapter_not_portal_role(provider):
    """claude 和 kimi 的权限预设与设置项由适配器校验，不支持的取值被拒绝"""
    definition = AGENT_PROVIDERS[provider]
    adapter = definition.runtime_adapter()
    adapter.validate_requested_fields(definition, {"permission_preset": "full-access"})
    with pytest.raises(ValueError, match="does not support permission preset"):
        adapter.validate_requested_fields(definition, {"permission_preset": "auto-review"})
    with pytest.raises(ValueError, match="does not support settings"):
        adapter.validate_requested_fields(definition, {"service_tier": "priority"})


@pytest.mark.parametrize("owner", [True, False])
@pytest.mark.parametrize("preset", [None, "", "default", "full-access"])
def test_native_permission_selection_is_independent_of_owner_flag(owner, preset):
    """claude 和 kimi 的原生权限模式只由权限预设决定，与是否为所有者无关"""
    claude = ClaudeOptions()
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    settings = {} if preset is None else {"permission_preset": preset}
    expected = "dontAsk" if preset == "default" else "bypassPermissions"
    assert claude.native_options({"provider_profile": AGENT_PROVIDERS["claude"].default_route}, settings)["permission_mode"] == expected
    invocation = _Invocation(_Request({"runtime_settings": settings,
                                      "authorization_scope": {"mode": "A"}, "is_owner": owner}))
    prepared = KimiOptions().prepare(invocation)
    assert prepared.request.runtime_options["permission_mode"] == ("manual" if preset == "default" else "yolo")


@pytest.mark.parametrize("provider", ["codex", "claude", "kimi"])
def test_new_resident_defaults_and_capabilities_use_full_access(provider):
    """codex、claude、kimi 新建 Resident 默认使用完全访问权限"""
    definition = AGENT_PROVIDERS[provider]
    adapter = definition.runtime_adapter()
    settings = adapter.defaults(definition, {})
    assert definition.runtime_capabilities()["default_permission_label"] == "Full Access"
    if provider == "codex":
        options = adapter.prepare(_Invocation(_Request({"runtime_settings": settings}))).request.runtime_options
        assert options["approval_policy"] == "never"
        assert options["sandbox"] == "danger-full-access"
    else:
        assert settings["permission_preset"] == "full-access"
        assert adapter.native_permission_mode(settings) == ("bypassPermissions" if provider == "claude" else "yolo")


def test_codex_a_mode_preserves_selected_native_permission_profile():
    """授权模式 A 下 codex 保留用户选择的原生权限配置和审批策略"""
    invocation = _Invocation(_Request({
        "runtime_settings": {"permission_preset": "default", "permissions": ":workspace",
                             "sandbox": "workspace-write", "approval_policy": "on-request"},
        "authorization_scope": {"mode": "A", "approval_policy": "never"},
    }))
    options = CodexOptions().prepare(invocation).request.runtime_options
    assert options["permissions"] == ":workspace"
    assert options["approval_policy"] == "on-request"
    assert "sandbox_policy" not in options


@pytest.mark.parametrize("mode", [None, "", "dontAsk", "bypassPermissions"])
def test_claude_permission_mode_reaches_native_daemon_request(monkeypatch, mode):
    """claude 的权限模式原样传给原生守护进程，未指定时为 bypassPermissions"""
    from management_portal.claude_code_client import ClaudeCodeClient

    client = ClaudeCodeClient()
    captured = {}

    def request(method, payload=None, **_kwargs):
        captured.update(method=method, payload=payload)
        return {"session_id": "session-test", "response": "ok"}

    monkeypatch.setattr(client, "_request", request)
    client.run_turn(session_key="binding", session_id="", prompt="hello", cwd="/workspace",
                    config_dir="/config", provider_profile="test", model="test-model",
                    is_owner=False, **({} if mode is None else {"permission_mode": mode}))
    assert captured["method"] == "turn/run"
    assert captured["payload"]["permission_mode"] == (mode or "bypassPermissions")

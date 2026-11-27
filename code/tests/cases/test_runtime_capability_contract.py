from dataclasses import dataclass, field

import pytest

from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
from management_portal.execution.native_options import ClaudeOptions, CodexOptions, KimiOptions
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service.resident_execution_provider import capability_projection


@dataclass(frozen=True)
class _Request:
    runtime_options: dict


@dataclass(frozen=True)
class _Session:
    # BackendInvocation always carries its RuntimeSession; Kimi reads the
    # session's route to validate the model against that route's catalog.
    backend_route_id: str = ""


@dataclass(frozen=True)
class _Invocation:
    request: _Request
    session: _Session = field(default_factory=_Session)


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
    assert catalog["runtime_capabilities"]["permission_presets"] == ["restricted", "full-access"]
    assert catalog["runtime_capabilities"]["default_permission_preset"] == "full-access"
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
@pytest.mark.parametrize("claude_preset,kimi_preset", [
    (None, None), ("", ""), ("restricted", "manual"), ("full-access", "full-access"),
])
def test_native_permission_selection_is_independent_of_owner_flag(owner, claude_preset, kimi_preset):
    """claude 和 kimi 的原生权限模式只由权限预设决定，与是否为所有者无关"""
    claude = ClaudeOptions()
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    settings = {} if claude_preset is None else {"permission_preset": claude_preset}
    expected = "dontAsk" if claude_preset == "restricted" else "bypassPermissions"
    assert claude.native_options({"provider_profile": AGENT_PROVIDERS["claude"].default_route}, settings)["permission_mode"] == expected
    settings = {} if kimi_preset is None else {"permission_preset": kimi_preset}
    invocation = _Invocation(_Request({"runtime_settings": settings,
                                      "authorization_scope": {"mode": "A"}, "is_owner": owner}))
    prepared = KimiOptions().prepare(invocation)
    assert prepared.request.runtime_options["permission_mode"] == ("manual" if kimi_preset == "manual" else "yolo")


def test_restricted_presets_have_meaningful_names_and_default_is_not_a_preset():
    """各 Provider 的受限权限档用表达含义的键，default 不再是权限预设

    Codex 为 ask、Claude 为 restricted、Kimi 为 manual、Antigravity 为 portal-tools-only；
    运行设置规范化保留这些键，不再接受 default。跨 Provider 分叉时受限档换成目标 Provider 的受限档。

    :module: execution.providers
    :design: decision_8b2fa224616b
    """
    from management_portal.execution.resident_runtime_settings import (
        NATIVE_PERMISSION_PRESETS, PERMISSION_PRESETS, carried_permission_preset, normalize_settings,
    )

    restricted = {"codex": "ask", "claude": "restricted", "kimi": "manual", "antigravity": "portal-tools-only"}
    known = {item["id"] for item in PERMISSION_PRESETS} | NATIVE_PERMISSION_PRESETS
    for provider, definition in AGENT_PROVIDERS.items():
        capabilities = definition.runtime_capabilities()
        presets = capabilities["permission_presets"]
        assert "default" not in presets
        assert set(presets) <= known, provider
        if presets:
            assert capabilities["default_permission_preset"] in presets
        if provider in restricted:
            assert restricted[provider] in presets
            assert normalize_settings({"permission_preset": restricted[provider]})["permission_preset"] == restricted[provider]
            adapter = definition.runtime_adapter()
            with pytest.raises(ValueError, match="does not support permission preset"):
                adapter.validate_requested_fields(definition, {"permission_preset": "default"})
    ask = normalize_settings({"permission_preset": "ask"})
    assert (ask["permissions"], ask["profile"]) == (":workspace", "permissions:ask")
    # A stored Codex "default" profile reads as ask through its fields alone.
    assert normalize_settings({"permission_preset": "default", "profile": "permissions:default", "permissions": ":workspace",
                               "approvals_reviewer": "user", "approval_policy": "on-request"})["permission_preset"] == "ask"
    native = normalize_settings({"permission_preset": "restricted", "permissions": ":danger-full-access",
                                 "sandbox": "danger-full-access", "sandbox_explicit": True})
    assert (native["permission_preset"], native["permissions"], native["sandbox"]) == ("restricted", "", "")
    assert carried_permission_preset("restricted", ["ask", "auto-review", "full-access"]) == "ask"
    assert carried_permission_preset("ask", ["manual", "full-access"]) == "manual"
    assert carried_permission_preset("auto-review", ["restricted", "full-access"]) == ""
    assert carried_permission_preset("full-access", ["restricted", "full-access"]) == "full-access"


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
        "runtime_settings": {"permission_preset": "ask", "permissions": ":workspace",
                             "sandbox": "workspace-write", "approval_policy": "on-request"},
        "authorization_scope": {"mode": "A", "approval_policy": "never"},
    }))
    options = CodexOptions().prepare(invocation).request.runtime_options
    assert options["permissions"] == ":workspace"
    assert options["approval_policy"] == "on-request"
    assert "sandbox_policy" not in options


@pytest.mark.parametrize("preset", ["ask", "auto-review", "full-access", "read-only"])
def test_codex_workspace_tier_writes_every_writable_workspace_binding(preset):
    """Codex 受限档可写该轮所有可写绑定的 Workspace，Full Access 与只读不被 Portal 改动

    ask 与 auto-review 以 workspaceWrite 策略发送，writableRoots 为 filesystem_scope 中 access=write 的
    Workspace 绑定；只读绑定不进 writableRoots（该策略可读全机）。Full Access 与只读会话保持原样。

    :module: execution.providers
    :design: decision_8b2fa224616b
    """
    from management_portal.execution.resident_runtime_settings import normalize_settings, read_only_runtime_settings

    settings = (read_only_runtime_settings({}) if preset == "read-only"
                else normalize_settings({"permission_preset": preset}))
    scope = {"cwd": "/w/own", "paths": [
        {"path": "/w/folder", "access": "write", "source": "resident_folder"},
        {"path": "/w/own", "access": "write", "source": "resident_workspace"},
        {"path": "/w/own", "access": "write", "source": "workspace_binding"},
        {"path": "/w/lineage", "access": "write", "source": "workspace_binding"},
        {"path": "/w/lineage", "access": "write", "source": "workspace_binding"},
        {"path": "/w/reference", "access": "read", "source": "workspace_binding"},
        {"path": "relative", "access": "write", "source": "workspace_binding"},
    ]}
    options = CodexOptions().prepare(_Invocation(_Request({"runtime_settings": settings, "authorization_scope": scope}))).request.runtime_options
    assert "authorization_scope" not in options
    if preset in {"ask", "auto-review"}:
        assert "permissions" not in options and "sandbox" not in options
        assert options["sandbox_policy"] == {"type": "workspaceWrite", "writableRoots": ["/w/own", "/w/lineage"], "networkAccess": False}
        assert options["approval_policy"] == "on-request"
        assert options.get("approvals_reviewer") == ("auto_review" if preset == "auto-review" else "user")
    elif preset == "full-access":
        assert "sandbox_policy" not in options and options["permissions"] == ":danger-full-access"
    else:
        assert "sandbox_policy" not in options and options["permissions"] == ":read-only"


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

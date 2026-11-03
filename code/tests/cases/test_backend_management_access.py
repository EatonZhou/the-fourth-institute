from types import SimpleNamespace
import pytest

from management_portal.http_portal_session import PortalSessionHttp


def test_provider_catalog_does_not_probe_runtime(monkeypatch):
    """新增账号只读接入目录，运行程序不可用或很慢时仍能选择 Provider。

    :design: decision_7956d7cb4bde
    """
    from management_portal.execution import account_registrations, isa, provider_updates
    from management_portal import kimi_code_server

    def unexpected(*args, **kwargs):
        raise AssertionError("catalog lookup must not inspect installed runtimes")

    monkeypatch.setattr(account_registrations, "registrations", lambda: [])
    monkeypatch.setattr(kimi_code_server, "service_configured", lambda: False)
    monkeypatch.setattr(provider_updates, "provider_runtime_status", unexpected)
    monkeypatch.setattr(provider_updates, "preparation_status", unexpected)
    result = isa.list_providers(SimpleNamespace(argument={"include_runtime": False, "refresh": True}.get))
    providers = {p["provider"]: p for p in result["providers"]}
    assert "subscription_login" in providers["codex"]["kinds"]
    assert "service" not in providers["kimi"]["kinds"]
    assert providers["provider_api"]["kinds"] == ["api_profile"]
    assert all("runtime" not in p and "preparation" not in p for p in providers.values())
    observations = []
    monkeypatch.setattr(provider_updates, "provider_runtime_status",
                        lambda **kwargs: observations.append(kwargs) or {"codex": {"installed_version": "fixture"}})
    monkeypatch.setattr(provider_updates, "preparation_status", lambda provider: {"status": "ready"})
    full = isa.list_providers(SimpleNamespace(argument={"refresh": True}.get))["providers"]
    assert observations == [{"refresh": True}]
    assert next(p for p in full if p["provider"] == "codex")["runtime"]["installed_version"] == "fixture"
    assert all(p["preparation"]["status"] == "ready" for p in full)


@pytest.mark.parametrize("register", [False, True])
@pytest.mark.parametrize("deletion", [None, "DeleteProviderAccount", "DeleteModelApi"])
def test_backend_viewer_identity_exposes_read_flags_without_management_write(monkeypatch, register, deletion):
    """AI Backend 共享查看者可以发现管理入口，且不获得运维写权限。

    :module: execution.providers
    :design: decision_7956d7cb4bde
    :aspects: behavior
    """
    from management_portal import permission_system

    permitted = {"ListProviders", "ListProviderAccounts"}
    if register:
        permitted.add("RegisterProviderAccount")
    if deletion:
        permitted.add(deletion)
    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: instruction in permitted)
    monkeypatch.setattr(permission_system, "user_category", lambda actor: "member")
    monkeypatch.setattr(permission_system, "mode", lambda: "par")
    http = PortalSessionHttp(
        respond=lambda request, status, payload: payload, exception_response=None, read_json=None,
        session_actor=lambda request: "backend_viewer", access=lambda: SimpleNamespace(user=lambda actor: {}),
        avatar=lambda actor: None, deployment="dev", build_id="",
        preferences=lambda actor: {}, set_preferences=None, conversation_payload=None, workspace_adapter=None,
    )
    flags = http.me(None, None, "")["access"]["allows"]
    assert flags["ListProviders"] is True
    assert flags["ListProviderAccounts"] is True
    assert flags["UpdateProviderAccount"] is False
    assert flags["CreateUser"] is False
    assert flags["RegisterProviderAccount"] is register
    assert flags["DeleteProviderAccount"] is (deletion == "DeleteProviderAccount")
    assert flags["DeleteModelApi"] is (deletion == "DeleteModelApi")

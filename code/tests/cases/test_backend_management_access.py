from types import SimpleNamespace

from management_portal.http_portal_session import PortalSessionHttp


def test_backend_viewer_identity_exposes_read_flags_without_management_write(monkeypatch):
    """AI Backend 共享查看者可以发现管理入口，且不获得运维写权限。

    :module: execution.providers
    :design: decision_7956d7cb4bde
    :aspects: behavior
    """
    from management_portal import permission_system

    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: instruction in {"ListProviders", "ListProviderAccounts"})
    monkeypatch.setattr(permission_system, "user_category", lambda actor: "member")
    monkeypatch.setattr(permission_system, "mode", lambda: "par")
    http = PortalSessionHttp(
        respond=lambda request, status, payload: payload, exception_response=None, read_json=None,
        session_actor=lambda request: "backend_viewer", access=lambda: SimpleNamespace(user=lambda actor: {}),
        avatar=lambda actor: None, instance="dev", build_id="", release_manifest={},
        preferences=lambda actor: {}, set_preferences=None, conversation_payload=None, workspace_adapter=None,
    )
    flags = http.me(None, None, "")["access"]["allows"]
    assert flags["ListProviders"] is True
    assert flags["ListProviderAccounts"] is True
    assert flags["UpdateProviderAccount"] is False
    assert flags["CreateUser"] is False

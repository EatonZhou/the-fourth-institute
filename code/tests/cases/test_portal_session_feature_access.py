"""Session identity reads feature page permissions without loading tool runtimes."""
from types import SimpleNamespace

import pytest

from management_portal import permission_system, portal_features
from management_portal.http_portal_session import PortalSessionHttp


@pytest.mark.parametrize("installed", [True, False])
def test_me_does_not_import_application_tool_implementations(monkeypatch, installed):
    telepus = next(feature for feature in portal_features.FEATURES if feature.key == "telepus")
    monkeypatch.setattr(portal_features, "FEATURES", (telepus,) if installed else ())
    portal_features.contributions.cache_clear()
    original_import = portal_features.importlib.import_module

    def import_page_contract(name, *args, **kwargs):
        if name in {"management_portal.telepus.tools", "management_portal.application_tools"}:
            raise AssertionError("session identity must not import application tool implementations")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(portal_features.importlib, "import_module", import_page_contract)
    checked = {}

    def allows(actor, name, arguments=None):
        checked[name] = arguments
        return name == "GetTelepusStatus"

    monkeypatch.setattr(permission_system, "allows", allows)
    monkeypatch.setattr(permission_system, "user_category", lambda actor: "member")
    monkeypatch.setattr(permission_system, "mode", lambda: "par")
    session = PortalSessionHttp(
        respond=lambda request, status, payload: (status, payload),
        exception_response=None, read_json=None, session_actor=lambda request: "alice",
        access=lambda: SimpleNamespace(user=lambda actor: {"status": "active"}),
        avatar=lambda actor: "", deployment="dev", build_id="test",
        preferences=lambda actor: {}, set_preferences=None,
        conversation_payload=None, workspace_adapter=None,
    )
    try:
        status, payload = session.me(None, None, None)
        assert status == 200 and payload["username"] == "alice"
        flags = payload["access"]["allows"]
        if installed:
            assert flags["GetTelepusStatus"] is True
            assert flags["SetTelepusEnabled"] is False
            assert checked["SetTelepusEnabled"] == {"enabled": True}
        else:
            assert "GetTelepusStatus" not in flags and "SetTelepusEnabled" not in flags
    finally:
        portal_features.contributions.cache_clear()

"""Native workbench HTTP uses the authenticated Resident, including its shell."""
import asyncio
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pytest

from management_portal import asgi, permission_system, serve_management_portal as portal, society_service
from management_portal import http_auth, user_preferences
from management_portal.workbench_projections import http as workbench_http, native
from tests.cases.security_http_support import server
from tests.cases.test_portal_asgi import _request

RESIDENT = "resident_0123456789ab"
LINEAGE = "lineage_0123456789ab"
WORKBENCH = "workbench_0123456789ab"
HEADERS = {"Authorization": "Bearer fixture-resident", "X-Portal-Lineage-ID": LINEAGE}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


@pytest.fixture
def browser_http(monkeypatch):
    state = SimpleNamespace(enabled=True, member=True, readable=True)
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request:
                        "viewer" if request.headers.get("Cookie") == "fixture=user" else None)
    monkeypatch.setattr(http_auth.agent_token_auth, "authenticate_bearer_token", lambda token, **kwargs:
                        {"agent_id": RESIDENT} if state.enabled and token == "fixture-resident" else None)

    def membership(lineage_id, actor):
        if not state.member or (lineage_id, actor) != (LINEAGE, RESIDENT):
            raise PermissionError("Resident is not a member")

    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        lineage=SimpleNamespace(authorize_lineage_resident_principal=membership)))

    def read(app_id, actor, **kwargs):
        if not state.readable:
            raise PermissionError("Workbench access denied")
        return {"workbench_id": app_id, "observed_actor": actor, "revision": 2}

    def app_state(app_id, actor, value=None, **kwargs):
        read(app_id, actor)
        return {"actor": actor, "value": value or {}}

    monkeypatch.setattr(workbench_http, "get_service", lambda: SimpleNamespace(
        store=SimpleNamespace(get=read, latest_generation=lambda *a: None,
                              authorized=lambda *a: True, state=app_state),
        summary=lambda app: {"url": "/workbenches"}))
    monkeypatch.setattr(native, "with_binding_status", lambda app, actor: app)
    monkeypatch.setattr(user_preferences, "_portal_user_preferences", lambda actor: {"owner": actor})
    monkeypatch.setattr(permission_system, "allows", lambda *a, **kw: False)
    monkeypatch.setattr(permission_system, "user_category", lambda *a: "member")
    monkeypatch.setattr(permission_system, "mode", lambda: "enabled")
    monkeypatch.setattr(portal._PORTAL_SESSION_HTTP, "avatar", lambda actor: {})

    def provision(actor):
        assert actor == "viewer", "Resident must not provision a human AMA"

    monkeypatch.setattr(portal._PORTAL_SESSION_HTTP, "session_opened", provision)

    class QuietHandler(portal.Handler):
        def log_message(self, *_args):
            pass

    with server(QuietHandler) as instance:
        origin = f"http://127.0.0.1:{instance.server_port}"

        def request(path, *, headers=None, payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            supplied = {**HEADERS, "Content-Type": "application/json", **(headers or {})}
            try:
                response = build_opener(NoRedirect).open(Request(origin + path, data=body, headers=supplied), timeout=10)
            except HTTPError as exc:
                response = exc
            with response:
                raw = response.read()
                value = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw.decode()
                return response.status, value, response.headers

        yield SimpleNamespace(request=request, state=state, origin=origin)


def test_native_document_and_detail_accept_resident_without_user_cookie(browser_http):
    status, body, headers = browser_http.request(f"/workbenches?panel=settings&id={WORKBENCH}")
    assert status == 200
    assert 'id="projectionContent"' in body
    assert "Set-Cookie" not in headers
    status, body, _ = browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}")
    assert status == 200 and body["observed_actor"] == RESIDENT


@pytest.mark.parametrize("path", ["/workbenches", "/workbenches/create", "/static/portal_workbench_context.js"])
def test_anonymous_native_entry_keeps_login_redirect(browser_http, path):
    status, _, headers = browser_http.request(path, headers={"Authorization": ""})
    assert status == 302 and headers["Location"] == "/login"


def test_sdk_state_ignores_payload_actor_and_keeps_resource_acl(browser_http):
    status, body, _ = browser_http.request("/api/workbenches/state", payload={
        "id": WORKBENCH, "actor": "admin", "value": {"selection": "task"}})
    assert status == 200 and body == {"actor": RESIDENT, "value": {"selection": "task"}}
    browser_http.state.readable = False
    status, _, _ = browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}")
    assert status == 403


def test_native_script_and_chat_dependencies_preserve_identity(browser_http, monkeypatch):
    status, body, _ = browser_http.request("/static/portal_workbench_context.js")
    assert status == 200 and "PortalWorkbenchContext" in body
    assert browser_http.request("/static/portal_workbench_context.js", headers={"Authorization": "Bearer invalid"})[0] == 401
    monkeypatch.setattr(portal._LINEAGE_HTTP, "lineage", lambda: SimpleNamespace(list_lineages=lambda actor: {"actor": actor}))
    assert browser_http.request("/lineage/api/lineages")[1] == {"actor": RESIDENT}
    monkeypatch.setattr(portal._AGENT_WORKBENCH_HTTP, "resident_api", lambda: SimpleNamespace(
        get_resident_session_projection=lambda target, actor: {"target": target, "actor": actor}))
    monkeypatch.setattr(portal._AGENT_WORKBENCH_HTTP, "resident_chat_projection", lambda: SimpleNamespace(
        project_session=lambda result, actor: {**result, "viewer": actor}))
    assert browser_http.request(f"/api/agent-workbench/residents/projection?id={RESIDENT}")[1] == {
        "target": RESIDENT, "actor": RESIDENT, "viewer": RESIDENT}
    monkeypatch.setattr(portal._RESOURCE_HTTP, "reference_payload", lambda address, actor: {"address": address, "viewer": actor})
    assert browser_http.request(f"/api/resources/reference?address={WORKBENCH}")[1] == {
        "address": WORKBENCH, "viewer": RESIDENT}


def test_chat_restart_options_use_resident_identity_and_keep_target_acl(browser_http, monkeypatch):
    # Chat fetches these options on mount, even before opening its restart menu.
    # A user-only route would turn a valid Resident workbench into a login page.
    def options(target, actor):
        if not browser_http.state.readable:
            raise PermissionError("Resident management denied")
        return {"resident_id": target, "viewer": actor, "strategies": []}

    monkeypatch.setattr(portal._AGENT_WORKBENCH_HTTP, "resident_api", lambda: SimpleNamespace(
        get_resident_restart_options=options))
    path = f"/api/agent-workbench/residents/session-restart-options?id={RESIDENT}"
    status, body, headers = browser_http.request(path)
    assert status == 200
    assert body == {"resident_id": RESIDENT, "viewer": RESIDENT, "strategies": []}
    assert "Set-Cookie" not in headers
    browser_http.state.readable = False
    assert browser_http.request(path)[0] == 403


@pytest.mark.parametrize("headers,status", [
    ({"Authorization": ""}, 401),
    ({"Authorization": "Bearer invalid"}, 401),
    ({"X-Portal-Lineage-ID": ""}, 403),
])
def test_chat_restart_options_reject_invalid_principal(browser_http, monkeypatch, headers, status):
    def unexpected_access():
        pytest.fail("Invalid principal reached restart options")

    monkeypatch.setattr(portal._AGENT_WORKBENCH_HTTP, "resident_api", unexpected_access)
    path = f"/api/agent-workbench/residents/session-restart-options?id={RESIDENT}"
    assert browser_http.request(path, headers=headers)[0] == status


@pytest.mark.parametrize("headers,status", [
    ({"Authorization": ""}, 401),
    ({"Authorization": "Bearer invalid"}, 401),
    ({"X-Portal-Lineage-ID": ""}, 403),
    ({"X-Portal-Lineage-ID": "lineage_ffffffffffff"}, 403),
])
def test_identity_rejected_before_resource_access(browser_http, headers, status):
    assert browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}", headers=headers)[0] == status


def test_revocation_and_membership_checked_on_next_request(browser_http):
    assert browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}")[0] == 200
    browser_http.state.enabled = False
    assert browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}")[0] == 401
    browser_http.state.enabled = True
    browser_http.state.member = False
    assert browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}")[0] == 403


def test_identity_and_preferences_use_resident_without_human_provisioning(browser_http):
    status, body, _ = browser_http.request("/api/me")
    assert status == 200
    assert body["username"] == RESIDENT
    assert body["principal"] == {"kind": "resident", "id": RESIDENT}
    assert body["preferences"] == {"owner": RESIDENT}
    assert browser_http.request("/api/preferences")[1] == {"preferences": {"owner": RESIDENT}}


def test_user_cookie_priority_origin_check_and_private_account_routes(browser_http):
    status, body, _ = browser_http.request(f"/api/workbenches/detail?id={WORKBENCH}", headers={"Cookie": "fixture=user"})
    assert status == 200 and body["observed_actor"] == "viewer"
    status, _, _ = browser_http.request("/api/workbenches/state", headers={
        "Cookie": "fixture=user", "Origin": "https://other.example"}, payload={"id": WORKBENCH})
    assert status == 403
    assert browser_http.request("/api/account/security")[0] == 401


@pytest.mark.parametrize("headers,status", [(HEADERS, 503), ({}, 302),
    ({**HEADERS, "X-Portal-Lineage-ID": "wrong"}, 403),
    ({**HEADERS, "Authorization": "Bearer invalid"}, 401)])
def test_event_stream_authentication_uses_same_resident_boundary(browser_http, monkeypatch, headers, status):
    # A valid identity reaches stream availability; invalid identity never does.
    monkeypatch.setattr(asgi, "ensure_notification_listener", lambda: None)
    monkeypatch.setattr(asgi, "ensure_knowledge_archive_projection_watcher", lambda: None)
    request = _request("/api/events", headers=headers, app=SimpleNamespace(state=SimpleNamespace()))
    response = asyncio.run(asgi.portal_events(request))
    assert response.status_code == status

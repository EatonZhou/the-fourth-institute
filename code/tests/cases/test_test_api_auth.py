from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import society_service, serve_management_portal
from management_portal import http_auth, web_accounts
from management_portal.test_framework import web as test_web


RESIDENT_ID = "resident_0123456789ab"
LINEAGE_ID = "lineage_0123456789ab"


class QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format, *_args):
        return


@pytest.fixture
def test_api(monkeypatch):
    state = SimpleNamespace(agent_id=RESIDENT_ID, identity_error=None)

    def authenticate(token, *, scope=""):
        if token == "test-resident-token":
            return {"agent_id": state.agent_id}
        return None

    def authorize(lineage_id, resident_id):
        if state.identity_error:
            raise state.identity_error
        if lineage_id != LINEAGE_ID or resident_id != RESIDENT_ID:
            raise PermissionError("Resident identity belongs to another Lineage")
        return resident_id

    def username(handler):
        return {"session=viewer": "viewer", "session=operator": "operator"}.get(
            handler.headers.get("Cookie", "")
        )

    resident = mock.Mock()
    resident.authorize_lineage_resident_principal.side_effect = authorize
    monkeypatch.setattr(
        society_service, "get_society_service",
        lambda: SimpleNamespace(lineage=resident),
    )
    monkeypatch.setattr(http_auth, "_get_session_username", username)
    monkeypatch.setattr(
        http_auth.agent_token_auth, "authenticate_bearer_token", authenticate,
    )
    access = mock.Mock()
    access.is_active.return_value = True
    monkeypatch.setattr(web_accounts, "_PORTAL_ACCESS", access)
    api = SimpleNamespace(**{
        name: mock.Mock(return_value={"ok": True})
        for name in (
            "query", "query_workspace", "query_overview", "query_page", "query_case_ids", "case_definition", "preview", "start", "get_run", "cancel_run",
            "delete_runs", "rotate_tau_resident", "configure_tau_residents", "tau_resident_status",
            "tau_admission", "set_tau_admission",
            "implementation_source",
        )
    })
    monkeypatch.setattr(test_web, "test_framework_api", api)
    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    def request(path, *, method="GET", payload=None, headers=None):
        request_headers = {
            "Authorization": "Bearer test-resident-token",
            "X-Portal-Lineage-ID": LINEAGE_ID,
            "Content-Type": "application/json",
        }
        if headers is not None:
            request_headers.update(headers)
        data = json.dumps(payload or {}).encode() if method == "POST" else None
        request = urllib.request.Request(
            base_url + path, data=data, method=method, headers=request_headers,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            return response.status, json.loads(body) if "application/json" in content_type else None

    yield SimpleNamespace(
        request=request, api=api, state=state, resident=resident, base_url=base_url,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize(("method", "path", "endpoint", "payload"), [
    ("GET", "/test/api/catalog", "query", {}),
    ("GET", "/test/api/source?case_id=test_0123456789ab&implementation_id=impl-example", "implementation_source", {}),
    ("GET", "/test/api/run?run_id=testrun_0123456789abcdef", "get_run", {}),
    ("POST", "/test/api/preview", "preview", {"select": {}}),
    ("POST", "/test/api/run", "start", {"select": {}, "actor": "CASE-A"}),
    ("POST", "/test/api/run/cancel", "cancel_run", {"run_id": "testrun_0123456789abcdef"}),
    ("POST", "/test/api/runs/delete", "delete_runs", {"run_ids": ["testrun_0123456789abcdef"]}),
    ("POST", "/test/api/tau/resident/rotate", "rotate_tau_resident", {"module_id": "workbench.chat"}),
    ("POST", "/test/api/tau/resident/configure", "configure_tau_residents", {"module_ids": ["workbench.chat"], "backend_route_id": "five", "fast": True}),
    ("GET", "/test/api/tau/admission", "tau_admission", {}),
    ("POST", "/test/api/tau/admission", "set_tau_admission", {"paused": True, "expected_revision": 7}),
])
def test_resident_test_routes_preserve_authenticated_actor(test_api, method, path, endpoint, payload):
    """Resident 调用各测试接口时以认证的 Resident 身份执行，忽略请求里的 actor"""
    status, result = test_api.request(path, method=method, payload=payload)
    assert status == 200
    assert result["ok"]
    assert getattr(test_api.api, endpoint).call_args.kwargs["actor"] == RESIDENT_ID
    test_api.resident.authorize_lineage_resident_principal.assert_called_once_with(LINEAGE_ID, RESIDENT_ID)


def test_tau_admission_route_passes_revision_and_boolean_to_domain(test_api):
    status, result = test_api.request("/test/api/tau/admission", method="POST",
                                      payload={"paused": True, "expected_revision": 7})
    assert status == 200 and result["ok"]
    test_api.api.set_tau_admission.assert_called_once_with(True, 7, actor=RESIDENT_ID)


def test_tau_admission_application_enforces_portal_admin(monkeypatch):
    from management_portal.test_framework import api
    from management_portal.permission_system import administrators
    from management_portal.society_service import application_target_configuration as target

    monkeypatch.setattr(administrators, "is_administrator", lambda actor: actor == "admin")
    update = mock.Mock(return_value={"admission_state": "paused", "revision": 8})
    monkeypatch.setattr(target, "update", update)
    with pytest.raises(PermissionError):
        api.set_tau_admission(True, 7, actor=RESIDENT_ID)
    with pytest.raises(ValueError):
        api.set_tau_admission("true", 7, actor="admin")
    result = api.set_tau_admission(True, 7, actor="admin")
    assert result["configuration"]["revision"] == 8
    update.assert_called_once_with("tau", {"admission_paused": True, "reason": "Test 页面暂停"},
                                   "admin", expected_revision=7)


def test_tau_admission_conflict_returns_409(test_api):
    from management_portal.society_service.application_target_configuration import ConfigurationConflict

    test_api.api.set_tau_admission.side_effect = ConfigurationConflict("Application configuration revision changed")
    status, result = test_api.request("/test/api/tau/admission", method="POST",
                                      payload={"paused": True, "expected_revision": 7})
    assert status == 409
    assert result["code"] == "instruction.conflict"


@pytest.mark.parametrize(("path", "endpoint"), [
    ("/test/api/catalog", "query"),
    ("/test/api/catalog/query", "query_case_ids"),
])
@pytest.mark.parametrize("has_binding", ["true", "false", None])
def test_catalog_routes_preserve_exact_point_and_binding_filters(test_api, path, endpoint, has_binding):
    """目录查询原样转发设计点引用和是否绑定设计点的筛选条件"""
    point_ref = "decision_8273271b67b1#0123456789abcdef"
    filters = {"design_point_ref": point_ref}
    if has_binding is not None:
        filters["has_design_point_ref"] = has_binding

    status, result = test_api.request(f"{path}?{urlencode(filters)}")

    assert status == 200
    assert result["ok"]
    forwarded = getattr(test_api.api, endpoint).call_args.args[0]
    assert forwarded["design_point_ref"] == point_ref
    if has_binding is None:
        assert "has_design_point_ref" not in forwarded
    else:
        assert forwarded["has_design_point_ref"] is (has_binding == "true")
    test_api.resident.authorize_lineage_resident_principal.assert_called_once_with(LINEAGE_ID, RESIDENT_ID)


def test_workspace_catalog_and_case_detail_use_bounded_read_projections(test_api):
    """工作区目录和用例详情分别走工作区投影与单个用例读取"""
    status, result = test_api.request("/test/api/catalog?projection=workspace")
    assert status == 200
    assert result["ok"]
    test_api.api.query_workspace.assert_called_once()
    assert test_api.api.query_workspace.call_args.kwargs == {"actor": RESIDENT_ID}
    assert test_api.api.query_workspace.call_args.args[0]["modules"] == []

    status, result = test_api.request("/test/api/catalog/case?case_id=test_0123456789ab")
    assert status == 200
    assert result == {"case": {"ok": True}}
    test_api.api.case_definition.assert_called_once_with("test_0123456789ab")


def test_test_page_overview_and_catalog_pages_pass_the_caller_and_filters(test_api):
    """Test 页面的总览按调用者读取，目录页把筛选、执行方式、仅当前与分页参数交给框架，分页参数无效时拒绝"""
    status, result = test_api.request("/test/api/catalog?projection=overview")
    assert status == 200 and result["ok"]
    test_api.api.query_overview.assert_called_once_with(actor=RESIDENT_ID)

    status, result = test_api.request("/test/api/catalog?projection=page&offset=100&limit=50&module=lineage.hatg&runner=none&search=route")
    assert status == 200 and result["ok"]
    filters = test_api.api.query_page.call_args.args[0]
    assert (filters["modules"], filters["runners"], filters["search"]) == (["lineage.hatg"], ["none"], "route")
    assert test_api.api.query_page.call_args.kwargs == {"offset": 100, "limit": 50}

    status, _result = test_api.request("/test/api/catalog/query?current=true&runner=tau")
    assert status == 200
    assert test_api.api.query_case_ids.call_args.args[0]["current"] is True
    assert test_api.api.query_case_ids.call_args.args[0]["runners"] == ["tau"]

    status, result = test_api.request("/test/api/catalog?projection=page&offset=many")
    assert status == 400
    assert test_api.api.query_page.call_count == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("authorization", ["", "Bearer invalid-token"])
def test_test_routes_reject_missing_or_invalid_credentials(test_api, method, authorization):
    """缺少凭据或令牌无效时测试接口返回 401"""
    status, _result = test_api.request(
        "/test/api/run", method=method, headers={"Authorization": authorization},
    )
    assert status == 401
    test_api.api.start.assert_not_called()
    test_api.api.get_run.assert_not_called()
    test_api.resident.authorize_lineage_resident_principal.assert_not_called()


def test_unknown_test_post_keeps_auth_gate_and_json_404(test_api):
    """未知的测试 POST 操作先过认证，通过后返回 JSON 格式的 404"""
    status, result = test_api.request("/test/api/unknown-action", method="POST")
    assert status == 404
    assert result["error"] == "unknown Test Lab action"
    assert result["http_status"] == 404

    status, result = test_api.request(
        "/test/api/unknown-action", method="POST", headers={"Authorization": ""},
    )
    assert status == 401
    assert result["error"] == "agent token or human session required"
    assert result["http_status"] == 401


@pytest.mark.parametrize("lineage_id", ["", "lineage_aaaaaaaaaaaa"])
def test_resident_test_routes_require_active_claimed_lineage(test_api, lineage_id):
    """Resident 请求的 Lineage 缺失或不符时测试接口返回 403"""
    status, _result = test_api.request(
        "/test/api/run", method="POST", headers={"X-Portal-Lineage-ID": lineage_id},
    )
    assert status == 403
    test_api.api.start.assert_not_called()


@pytest.mark.parametrize("error", [
    PermissionError("Resident identity is deleted"),
    KeyError("Resident identity is unavailable"),
])
def test_resident_test_routes_reject_inactive_identity(test_api, error):
    """Resident 身份已删除或不可用时测试请求返回 403"""
    test_api.state.identity_error = error
    status, _result = test_api.request("/test/api/run", method="POST")
    assert status == 403
    test_api.api.start.assert_not_called()


def test_resident_test_routes_fail_closed_on_identity_service_outage(test_api):
    """身份服务不可用时 Resident 的测试请求返回 503 且不执行"""
    test_api.state.identity_error = RuntimeError("SocietyService unavailable")
    status, _result = test_api.request("/test/api/run", method="POST")
    assert status == 503
    test_api.api.start.assert_not_called()


def test_agent_token_cannot_claim_human_identity(test_api):
    """agent 令牌冒用人类用户身份时 Test API 返回 403"""
    test_api.state.agent_id = "CASE-A"
    status, _result = test_api.request("/test/api/run", method="POST")
    assert status == 403
    test_api.api.start.assert_not_called()


def test_a_resident_token_may_operate_the_test_page(test_api):
    """Resident 令牌可以查看测试目录并发起运行"""
    status, catalog = test_api.request("/test/api/catalog")
    assert status == 200
    assert catalog["access"] == {"actor": RESIDENT_ID, "can_operate": True}
    assert test_api.request("/test/api/run", method="POST")[0] == 200
    test_api.api.start.assert_called_once_with({}, actor=RESIDENT_ID)


def test_bearer_test_access_does_not_open_other_browser_routes(test_api):
    """Test API 的 Bearer 令牌不能访问其他浏览器路由"""
    assert test_api.request("/api/terminal/start", method="POST")[0] == 401


def test_tau_configuration_status_is_readable_but_mutations_require_operation_rights(test_api):
    """TAU Resident 配置状态可读取，有运行权限的会话可以修改配置"""
    status, result = test_api.request("/test/api/tau/residents?module=workbench.chat")
    assert status == 200
    assert result["ok"]
    test_api.api.tau_resident_status.assert_called_once_with(["workbench.chat"])
    headers = {"Cookie": "session=viewer", "Origin": test_api.base_url}
    assert test_api.request("/test/api/tau/resident/configure", method="POST", headers=headers)[0] == 200
    test_api.api.configure_tau_residents.assert_called_once()


def test_human_session_retains_priority_capability_checks_and_csrf(test_api):
    """人类会话以会话用户身份发起运行，来源不可信的 POST 被拒绝"""
    headers = {"Cookie": "session=viewer", "Origin": test_api.base_url}
    status, catalog = test_api.request("/test/api/catalog", headers=headers)
    assert status == 200
    assert catalog["access"] == {"actor": "viewer", "can_operate": True}
    assert test_api.request("/test/api/run", method="POST", headers=headers)[0] == 200
    test_api.api.start.assert_called_once_with({}, actor="viewer")
    test_api.api.start.reset_mock()
    headers["Cookie"] = "session=operator"
    assert test_api.request("/test/api/run", method="POST", headers=headers)[0] == 200
    test_api.api.start.assert_called_once_with({}, actor="operator")
    headers["Origin"] = "https://untrusted.invalid"
    assert test_api.request("/test/api/run", method="POST", headers=headers)[0] == 403
    test_api.api.start.assert_called_once()


def test_resident_cannot_cancel_another_actors_run(test_api):
    """Resident 取消他人发起的测试运行时返回 403"""
    test_api.api.cancel_run.side_effect = PermissionError("只有发起者可以取消运行")
    status, result = test_api.request(
        "/test/api/run/cancel", method="POST", payload={"run_id": "testrun_0123456789abcdef"},
    )
    assert status == 403
    assert "发起者" in result["error"]
    test_api.api.cancel_run.assert_called_once_with("testrun_0123456789abcdef", actor=RESIDENT_ID)

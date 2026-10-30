from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlparse

import pytest

from management_portal import http_paper, paper_registration, serve_management_portal as server
from management_portal import http_auth, http_overview, http_request_body, http_responses, http_static_files, web_accounts
from management_portal.resource_backup_backend import ResourceBackupBackend
from tests.cases.test_permission_entrypoints import checkpoint_entries

D = 'article_111111111111'






def test_paper_upload_rejects_oversized_multipart_before_mutation(monkeypatch):
    """论文上传在修改前拒绝超限 multipart 请求并返回 413"""
    upload = mock.Mock()
    monkeypatch.setattr(paper_registration, "upload_paper_pdf", upload)
    monkeypatch.setattr(http_auth, "_get_agent_api_auth", lambda handler: {"type": "human", "id": "bob"})
    monkeypatch.setattr(http_paper, "authorize_api", lambda handler, auth, **_: "bob")
    monkeypatch.setattr(
        http_request_body, "_read_request_multipart",
        mock.Mock(side_effect=http_request_body.RequestBodyTooLarge("fixture upload too large")),
    )
    monkeypatch.setattr(http_responses, "_serve_json", lambda handler, status, value: (status, value))
    parsed = urlparse("/paper/api/papers/upload-pdf")
    result = server._PORTAL_HTTP_ROUTES.dispatch(
        "POST", parsed.path,
        SimpleNamespace(headers={"Content-Type": "multipart/form-data; boundary=fixture"}),
        parsed, "bob", phase="dual-auth",
    )
    assert result == (413, {"error": "fixture upload too large"})
    upload.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unknown_paper_api_keeps_bearer_authorization_and_404(monkeypatch, method):
    """未知论文 API 先执行 bearer 鉴权再返回 404"""
    auth = {"type": "agent", "id": "resident_0123456789ab"}
    monkeypatch.setattr(http_auth, "_get_agent_api_auth", lambda handler: auth)
    authorize = mock.Mock(return_value=auth["id"])
    monkeypatch.setattr(http_paper, "authorize_api", authorize)
    monkeypatch.setattr(http_responses, "_serve_json", lambda handler, status, payload: (status, payload))
    monkeypatch.setattr(http_request_body, "_read_request_json", lambda handler: {})
    parsed = urlparse("/paper/api/unknown-operation")
    result = server._PORTAL_HTTP_ROUTES.dispatch(
        method, parsed.path, SimpleNamespace(headers={}), parsed, "", phase="dual-auth",
    )
    assert result == (404, {"error": "API route not found"})
    assert authorize.call_args.args[1] is auth


def test_unknown_paper_api_rejects_missing_auth_without_claiming_other_routes(monkeypatch):
    """未知论文 API 缺少认证时拒绝且不接管其他路由"""
    monkeypatch.setattr(http_auth, "_get_agent_api_auth", lambda handler: None)
    monkeypatch.setattr(http_responses, "_serve_json", lambda handler, status, payload: (status, payload))
    request = SimpleNamespace(headers={})
    paper = urlparse("/paper/api/unknown-operation")
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", paper.path, request, paper, "", phase="dual-auth",
    )[0] == 401
    other = urlparse("/unknown/api/operation")
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", other.path, request, other, "", phase="dual-auth",
    ) is server.ROUTE_NOT_FOUND


def test_principal_backpack_routes_keep_explicit_operation_and_unknown_404(monkeypatch):
    """主体背包对未知与迁移接口均返回 404"""
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, payload: (status, payload))
    request = SimpleNamespace(path="/api/principal-backpack/unknown", headers={})
    unknown = urlparse(request.path)
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", unknown.path, request, unknown, "alice", phase="protected",
    ) == (404, {"error": "未知背包接口"})

    migration = urlparse("/api/principal-backpack/migration")
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", migration.path, request, migration, "alice", phase="protected",
    ) == (404, {"error": "未知背包接口"})


def test_public_static_routes_keep_asset_location_and_media_type(monkeypatch):
    """公共静态路由保留资源位置与媒体类型"""
    monkeypatch.setattr(http_static_files, "_serve_static_file", lambda request, path, media_type: (path, media_type))
    request = SimpleNamespace(headers={})
    for path, expected_root, expected_type in (
        ("/static/test_page.js", server.ROOT / "test_framework" / "page", "text/javascript; charset=utf-8"),
        ("/static/portal_projections.js", server.ROOT / "static", "application/javascript; charset=utf-8"),
        ("/static/portal_mailbox_todos.js", server.ROOT / "static", "text/javascript; charset=utf-8"),
    ):
        parsed = urlparse(path)
        result = server._PORTAL_HTTP_ROUTES.dispatch(
            "GET", parsed.path, request, parsed, "", phase="public",
        )
        assert result == (expected_root / path.rsplit("/", 1)[-1], expected_type)


def test_admin_access_is_read_only_and_keeps_actor(monkeypatch):
    """管理访问接口仅支持读取并传递当前身份

    :design: decision_30528aff7035"""
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "alice")
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, payload: (status, payload))
    monkeypatch.setattr(web_accounts, "_admin_access_payload", lambda actor: {"actor": actor})
    monkeypatch.setattr(http_request_body, "_read_request_json", lambda request: {"action": "grant"})
    action = mock.Mock(side_effect=FileExistsError("already granted"))
    monkeypatch.setattr(web_accounts, "_admin_access_action", action)
    parsed = urlparse("/api/admin/access-control")
    request = SimpleNamespace(path=parsed.path, headers={}, command="POST")
    monkeypatch.setattr(http_responses, "_serve_http_error", lambda request, status, message: (status, message))
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", parsed.path, request, parsed, "alice", phase="protected",
    ) == (200, {"actor": "alice"})
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "POST", parsed.path, request, parsed, "alice", phase="protected",
    ) == (404, "API route not found")
    action.assert_not_called()


def test_user_routes_keep_actor_and_response_contract(monkeypatch):
    """用户路由保留当前身份及响应结构"""
    request = SimpleNamespace(headers={})
    monkeypatch.setattr(http_auth, "_get_session_username", lambda handler: "alice")
    monkeypatch.setattr(http_overview, "list_users", lambda: {"users": ["alice"]})
    monkeypatch.setattr(http_responses, "_serve_json", lambda handler, status, payload: (status, payload))

    todos = urlparse("/api/users")
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", todos.path, request, todos, "alice", phase="protected",
    ) == (200, {"users": ["alice"]})




def test_stale_execution_receipt_does_not_replace_resource_authorization(checkpoint_entries, monkeypatch):
    """过期执行回执不阻止资源修改且变更记录标记身份不匹配"""
    from management_common.execution_association import execution_scope
    from management_portal.resources import content_changes
    from management_portal.resources.service import ResourceService

    fixture = checkpoint_entries
    execution = mock.Mock()
    execution.status.return_value = {"status": "completed", "request": {}}
    monkeypatch.setattr(content_changes, "get_execution_service", lambda: execution)
    registry = ResourceService()
    update = mock.Mock(return_value={"resource_id": D})
    registry.register_mutation_provider("article", update=update)
    monkeypatch.setattr(registry, "_notify_mutation", mock.Mock(return_value={}))
    with execution_scope("stale-execution"):
        result = registry.update(D, {"text": "allowed"}, actor="bob")
    update.assert_called_once()
    assert result["change_recording"] == {"status": "unconfirmed", "reason": "execution_actor_mismatch"}


def test_chat_context_options_have_an_independent_portal_route(monkeypatch):
    """聊天上下文选项通过独立 Portal 路由返回"""
    options = {"skills": [], "projects": []}
    monkeypatch.setattr(http_overview.agent_context_selection, "list_options", lambda: options)
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, payload: (status, payload))
    monkeypatch.setattr(http_auth, "_get_page_api_actor", lambda request: "alice")
    parsed = urlparse("/api/agent-context-options")
    assert server._PORTAL_HTTP_ROUTES.dispatch(
        "GET", parsed.path, SimpleNamespace(headers={}), parsed, "", phase="dual-auth",
    ) == (200, options)

"""HTTP contracts for review, skill, and article routes."""

from types import SimpleNamespace
from unittest.mock import ANY, Mock
from urllib.parse import urlparse

from management_portal import serve_management_portal as portal
from management_portal import article_files, article_git
from management_portal import http_auth, http_files, http_request_body, http_responses, web_resources


def _route(method: str, path: str):
    matches = [
        route for route in portal._portal_route_definitions()
        if route.method == method and route.path == path
    ]
    assert len(matches) == 1
    return matches[0]




def test_article_git_route_preserves_conflict_status_and_actor(monkeypatch):
    """文章 Git 暂存冲突时接口返回 409，并以会话用户检查资源权限"""
    require_resource = Mock()
    stage = Mock(side_effect=ValueError("index conflict"))
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "alice")
    monkeypatch.setattr(http_request_body, "_read_request_json", lambda request: {"paths": ["main.tex"]})
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, value: (status, value))
    monkeypatch.setattr(web_resources, "_require_resource", require_resource)
    monkeypatch.setattr(article_git,"stage_article_git_paths", stage)

    route = _route("POST", "/api/articles/{article_id}/git/{action}")
    path = "/api/articles/article_0123456789ab/git/stage"
    result = route.handler(SimpleNamespace(), urlparse(path), "ignored", route.template_params(path))
    assert result == (409, {"error": "index conflict"})
    require_resource.assert_called_once_with("article_0123456789ab", "alice")
    stage.assert_called_once_with("article_0123456789ab", ["main.tex"], staged=True)


def test_article_files_get_uses_registered_route_and_session_actor(monkeypatch):
    """文章文件列表接口经注册路由以会话用户检查权限后返回文件"""
    require_resource = Mock()
    listing = Mock(return_value={"files": ["main.tex"]})
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "alice")
    monkeypatch.setattr(web_resources, "_require_resource", require_resource)
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, value: (status, value))
    monkeypatch.setattr(article_files,"list_article_files", listing)

    route = _route("GET", "/api/articles/{article_id}/files")
    path = "/api/articles/article_0123456789ab/files"
    assert route.handler(SimpleNamespace(), urlparse(path), "ignored", route.template_params(path)) == (
        200, {"files": ["main.tex"]},
    )
    require_resource.assert_called_once_with("article_0123456789ab", "alice")
    listing.assert_called_once_with("article_0123456789ab")


def test_article_raw_get_keeps_media_response_and_resource_check(monkeypatch):
    """文章原始文件接口检查资源权限后按媒体类型流式返回文件"""
    require_resource = Mock()
    resolve = Mock(return_value="/articles/main.tex")
    media = Mock(return_value="streamed")
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "alice")
    monkeypatch.setattr(web_resources, "_require_resource", require_resource)
    monkeypatch.setattr(article_files,"resolve_article_raw_file", resolve)
    monkeypatch.setattr(http_files, "_media_content_type", lambda path: "text/plain")
    monkeypatch.setattr(http_files, "_serve_media_file", media)

    route = _route("GET", "/api/articles/{article_id}/raw")
    path = "/api/articles/article_0123456789ab/raw?path=main.tex"
    assert route.handler(SimpleNamespace(), urlparse(path), "ignored", route.template_params(urlparse(path).path)) == "streamed"
    require_resource.assert_called_once_with("article_0123456789ab", "alice")
    resolve.assert_called_once_with("article_0123456789ab", "main.tex")
    media.assert_called_once_with(ANY, "/articles/main.tex", "text/plain")


def test_article_git_history_get_keeps_error_status(monkeypatch):
    """文章 Git 历史接口无权限时返回 403 并传递 limit 参数"""
    monkeypatch.setattr(http_auth, "_get_session_username", lambda request: "alice")
    monkeypatch.setattr(web_resources, "_require_resource", Mock())
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, value: (status, value))
    history = Mock(side_effect=PermissionError("history denied"))
    monkeypatch.setattr(article_git,"article_git_history", history)

    route = _route("GET", "/api/articles/{article_id}/git/history")
    path = "/api/articles/article_0123456789ab/git/history?limit=3"
    assert route.handler(SimpleNamespace(), urlparse(path), "ignored", route.template_params(urlparse(path).path)) == (
        403, {"error": "history denied"},
    )
    history.assert_called_once_with("article_0123456789ab", limit=3)

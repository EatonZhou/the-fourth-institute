from __future__ import annotations

import json
from pathlib import Path

import pytest

from management_portal import paper_backend
from management_portal.http_routes import (
    ROUTE_NOT_FOUND,
    RouteDefinition,
    RouteDispatcher,
)
from management_portal.shared_state import atomic_create_json, atomic_replace_json


def test_atomic_replace_json_preserves_mode_and_writes_canonical_document(tmp_path: Path) -> None:
    """atomic_replace_json 保留原文件权限，写出排序后的 JSON 且不留临时文件"""
    target = tmp_path / "state.json"
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o640)

    atomic_replace_json(target, {"z": "中文", "a": 1})

    assert target.stat().st_mode & 0o777 == 0o640
    assert target.read_text(encoding="utf-8").endswith("\n")
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "z": "中文"}
    assert not list(tmp_path.glob(".state.json.*"))


def test_atomic_create_json_is_exclusive_and_canonical(tmp_path: Path) -> None:
    """atomic_create_json 只在文件不存在时创建，按指定权限写出排序后的 JSON"""
    target = tmp_path / "identity.json"
    atomic_create_json(target, {"z": "中文", "a": 1}, mode=0o440)

    assert target.stat().st_mode & 0o777 == 0o440
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "z": "中文"}
    with pytest.raises(FileExistsError):
        atomic_create_json(target, {"a": 2})




def test_route_dispatcher_enforces_owner_and_phase() -> None:
    """路由分发按认证阶段和路由归属匹配，模板参数不能为空，重复归属在构造时报错"""
    handler = lambda request, parsed, actor: (request, parsed, actor)
    dispatcher = RouteDispatcher(
        (
            RouteDefinition("GET", "/public", handler, "public", phase="public", page=True),
            RouteDefinition("GET", "/dual/", handler, "dual", phase="dual-auth", match="prefix"),
            RouteDefinition("POST", "/api/items/", handler, "items", match="prefix"),
        )
    )

    assert dispatcher.dispatch("GET", "/public", 1, 2, "", phase="public") == (1, 2, "")
    assert dispatcher.dispatch("GET", "/dual/item", 1, 2, "", phase="dual-auth") == (1, 2, "")
    assert dispatcher.dispatch("POST", "/api/items/42", 1, 2, "sample_admin", phase="protected") == (1, 2, "sample_admin")
    assert dispatcher.dispatch("GET", "/public", 1, 2, "", phase="protected") is ROUTE_NOT_FOUND
    assert dispatcher.page_paths() == ("/public",)
    for method, match in (("POST", "exact"), ("GET", "prefix"), ("GET", "template")):
        with pytest.raises(ValueError, match="explicit GET document"):
            RouteDefinition(method, "/pages/", handler, "pages", match=match, page=True)

    template_handler = lambda request, parsed, actor, params: (request, parsed, actor, params)
    templated = RouteDispatcher((
        RouteDefinition("GET", "/api/repos/{repo_id}/workspace", template_handler, "repos", match="template"),
        RouteDefinition("GET", "/api/repos/automation/status", handler, "automation"),
        RouteDefinition("POST", "/api/resources/{resource_id}/actions/{action}", template_handler, "resources", match="template"),
    ))
    assert templated.dispatch("GET", "/api/repos/repo_123/workspace", 1, 2, "sample_admin", phase="protected") == (1, 2, "sample_admin", {"repo_id": "repo_123"})
    assert templated.dispatch("GET", "/api/repos/repo_123/workspace/file", 1, 2, "sample_admin", phase="protected") is ROUTE_NOT_FOUND
    assert templated.dispatch("POST", "/api/resources/res_123/actions/edit", 1, 2, "sample_admin", phase="protected") == (1, 2, "sample_admin", {"resource_id": "res_123", "action": "edit"})
    assert templated.dispatch("POST", "/api/resources//actions/edit", 1, 2, "sample_admin", phase="protected") is ROUTE_NOT_FOUND
    with pytest.raises(RuntimeError, match="owned by both"):
        RouteDispatcher((
            RouteDefinition("GET", "/api/repos/{repo_id}/workspace", handler, "repos", match="template"),
            RouteDefinition("GET", "/api/repos/one/workspace", handler, "one"),
        ))

    with pytest.raises(RuntimeError, match="owned by both"):
        RouteDispatcher(
            (
                RouteDefinition("GET", "/same", handler, "first"),
                RouteDefinition("GET", "/same", handler, "second"),
            )
        )
    with pytest.raises(RuntimeError, match="owned by both"):
        RouteDispatcher(
            (
                RouteDefinition("GET", "/api/items/", handler, "collection", match="prefix"),
                RouteDefinition("GET", "/api/items/42", handler, "detail"),
            )
        )
    with pytest.raises(RuntimeError, match="owned by both"):
        RouteDispatcher(
            (
                RouteDefinition("POST", "/api/", handler, "api", match="prefix"),
                RouteDefinition("POST", "/api/items/", handler, "items", match="prefix"),
            )
        )

    fallback = lambda request, parsed, actor: "fallback"
    dispatcher = RouteDispatcher((
        RouteDefinition("GET", "/api/items/", lambda *_: ROUTE_NOT_FOUND, "items", match="prefix"),
        RouteDefinition("GET", "/", fallback, "fallback", match="fallback"),
    ))
    assert dispatcher.dispatch("GET", "/api/items/missing", None, None, "", phase="protected") == "fallback"
    with pytest.raises(RuntimeError, match="follows a fallback"):
        RouteDispatcher((
            RouteDefinition("GET", "/", fallback, "fallback", match="fallback"),
            RouteDefinition("GET", "/api/items", handler, "items"),
        ))


def test_reference_title_rules_are_ordered_and_share_candidate_validation(monkeypatch) -> None:
    """参考文献标题规则按顺序尝试，共用候选校验并跳过年份和 URL"""
    rules = (
        paper_backend._ReferenceTitleRule("invalid-year", lambda _raw: "2024"),
        paper_backend._ReferenceTitleRule("valid-title", lambda _raw: "Learning Robots from Data"),
        paper_backend._ReferenceTitleRule("never-reached", lambda _raw: "Wrong Result"),
    )
    monkeypatch.setattr(paper_backend, "_REFERENCE_TITLE_RULES", rules)

    assert paper_backend._guess_ref_title("reference") == "Learning Robots from Data"
    assert paper_backend._validated_ref_title_candidate("https://example.org/paper.pdf") == ""

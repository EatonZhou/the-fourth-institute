""":module: spec.design-governance"""
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import http_paper, paper_favorites, paper_library, presentation_store, serve_management_portal as portal
from management_portal import http_auth, web_accounts, web_resources
from tests.cases.test_resource_portal_routes import _server


PAPER = "paper_0123456789ab"
OTHER = "paper_1123456789ab"
HIDDEN = "paper_2123456789ab"


@pytest.fixture
def resources(monkeypatch):
    get = Mock(return_value={"resource_id": PAPER})
    monkeypatch.setattr(paper_favorites, "get_resource_service", lambda: SimpleNamespace(get=get))
    return get


def test_favorites_persist_by_actor_and_concurrent_updates_are_not_lost(resources):
    """论文收藏按用户持久化，并发添加不丢失，清空只影响本人"""
    ids = [f"paper_{index:012x}" for index in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: paper_favorites.set_favorite("alice", item, True), ids))
    paper_favorites.set_favorite("alice", ids[0], True)
    paper_favorites.set_favorite("bob", OTHER, True)
    persisted = {actor: paper_favorites.favorite_ids(actor) for actor in ('alice', 'bob')}
    assert set(persisted["alice"]) == set(ids)
    assert len(persisted["alice"]) == 12
    assert persisted["bob"] == [OTHER]
    assert presentation_store.delete_actor_favorites("alice")
    assert paper_favorites.favorite_ids("alice") == []
    assert paper_favorites.favorite_ids("bob") == [OTHER]


def test_add_checks_access_but_removal_survives_resource_access_loss(resources):
    """收藏论文时检查访问权限，失去访问权后仍可取消收藏"""
    paper_favorites.set_favorite("alice", PAPER, True)
    resources.assert_called_once_with(PAPER, actor="alice")
    resources.side_effect = PermissionError("Paper is not visible")
    with pytest.raises(PermissionError):
        paper_favorites.set_favorite("alice", OTHER, True)
    assert paper_favorites.favorite_ids("alice") == [PAPER]
    paper_favorites.set_favorite("alice", PAPER, False)
    assert paper_favorites.favorite_ids("alice") == []
    assert resources.call_count == 2


@pytest.mark.parametrize("paper_id,favorite", [("", True), ("project:foo", True), (PAPER, "false"), (PAPER, None), (PAPER, 1)])
def test_invalid_favorites_do_not_change_state(resources, paper_id, favorite):
    """论文 ID 或收藏值非法时报错，不检查权限也不改变收藏"""
    with pytest.raises(ValueError):
        paper_favorites.set_favorite("alice", paper_id, favorite)
    resources.assert_not_called()
    assert paper_favorites.favorite_ids("alice") == []


def test_library_favorite_filter_intersects_current_visibility_and_search_without_mutating_papers(resources, monkeypatch):
    """论文库收藏筛选与当前可见范围和检索条件取交集，不改动论文数据"""
    for item in [PAPER, HIDDEN]:
        paper_favorites.set_favorite("alice", item, True)
    papers = [
        {"id": PAPER, "title": "Attention model", "year": "2026", "venue": "Test"},
        {"id": OTHER, "title": "Attention second", "year": "2025", "venue": "Test"},
    ]
    visible = Mock(return_value=papers)
    monkeypatch.setattr(paper_library, "resource_index_papers", visible)
    monkeypatch.setattr(paper_library, "_all_reviews", lambda: [])
    result = paper_library.list_library({"favorite": "1", "q": "Attention", "year": "2026", "compact": "1"}, actor="alice")
    visible.assert_called_with(actor="alice")
    assert [item["id"] for item in result["papers"]] == [PAPER]
    assert result["summary"]["visible_count"] == 1
    assert set(result["favorite_ids"]) == {PAPER, HIDDEN}
    assert paper_library.list_library({"favorite": "1"}, actor="bob")["papers"] == []
    assert paper_library.list_library({"favorite": "1", "q": "absent"}, actor="alice")["papers"] == []
    assert len(paper_library.list_library({}, actor="alice")["papers"]) == 2
    assert all("favorite" not in item for item in papers)


def test_favorite_filter_keeps_absolute_semantic_order(resources, monkeypatch):
    """收藏筛选与语义检索同时使用时按语义排名排序"""
    for item in [PAPER, OTHER]:
        paper_favorites.set_favorite("alice", item, True)
    monkeypatch.setattr(paper_library, "resource_index_papers", lambda **_: [{"id": PAPER, "title": "A"}, {"id": OTHER, "title": "B"}])
    monkeypatch.setattr(paper_library, "_all_reviews", lambda: [])
    monkeypatch.setattr(paper_library.semantic_search_jobs, "search_view", lambda *a, **k: {
        "summary": {"status": "done"}, "result_map": {
            PAPER: {"rank": 8, "score": 65, "reason": "A"},
            OTHER: {"rank": 2, "score": 90, "reason": "B"},
        },
    })
    result = paper_library.list_library({"favorite": "1", "semantic": "search_0123456789ab"}, actor="alice")
    assert [(item["id"], item["semantic_rank"], item["semantic_score"]) for item in result["papers"]] == [(OTHER, 2, 90), (PAPER, 8, 65)]


def test_http_uses_authenticated_actor_without_the_paper_check(resources, monkeypatch):
    """Paper 收藏接口使用认证得到的 actor，忽略请求体里的 actor，且不做 Paper 资源检查。"""
    auth = {"username": "alice"}
    monkeypatch.setattr(http_auth, "_get_agent_api_auth", lambda request: auth)
    authorize = Mock(return_value="alice")
    monkeypatch.setattr(http_paper, "authorize_api", authorize)
    paper_check = Mock(side_effect=AssertionError("personal favorites must not check the Paper resource"))
    monkeypatch.setattr(web_resources, "_require_resource", paper_check)
    server, thread, origin = _server()
    try:
        def send(payload):
            request = urllib.request.Request(origin + "/paper/api/favorites", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            return urllib.request.urlopen(request)
        with send({"paper_id": PAPER, "favorite": True, "actor": "bob"}) as response:
            assert json.load(response) == {"paper_id": PAPER, "favorite": True}
        assert authorize.call_args.args[1] is auth
        assert paper_favorites.favorite_ids("alice") == [PAPER]
        assert paper_favorites.favorite_ids("bob") == []
        with pytest.raises(urllib.error.HTTPError) as invalid:
            send({"paper_id": PAPER, "favorite": "false"})
        assert invalid.value.code == 400
        resources.side_effect = PermissionError("not visible")
        with pytest.raises(urllib.error.HTTPError) as denied:
            send({"paper_id": OTHER, "favorite": True})
        assert denied.value.code == 403
        with send({"paper_id": PAPER, "favorite": False}) as response:
            assert response.status == 200
        auth.clear()
        with pytest.raises(urllib.error.HTTPError) as unauthenticated:
            send({"paper_id": PAPER, "favorite": True})
        assert unauthenticated.value.code == 401
        paper_check.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_account_personal_data_removal_includes_paper_favorites(resources, monkeypatch):
    """删除用户个人数据时一并清除其论文收藏，不影响其他用户"""
    paper_favorites.set_favorite("alice", PAPER, True)
    paper_favorites.set_favorite("bob", OTHER, True)
    monkeypatch.setattr(web_accounts, "_PORTAL_USER_UPLOAD_ROOTS", [])
    assert web_accounts._delete_portal_user_personal_data("alice")["favorites"]
    assert paper_favorites.favorite_ids("alice") == []
    assert paper_favorites.favorite_ids("bob") == [OTHER]

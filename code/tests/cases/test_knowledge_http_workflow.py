""":importance: P3"""
from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from knowledge_board_lan import knowledge_backend, knowledge_details, knowledge_source
from management_portal import serve_management_portal
from management_portal import http_auth, http_request_body, web_resources


def test_knowledge_projection_preserves_search_resource_type():
    from management_portal.resource_views import _knowledge_item
    item = _knowledge_item({
        "resource_id": "search_8176d8f8cf7a",
        "resource_type": "search",
        "title": "测试页面设计",
        "attributes": {"project_ids": []},
    })
    assert item["kb_id"] == "search_8176d8f8cf7a"
    assert item["resource_type"] == "search"


class _QuietHandler(serve_management_portal.Handler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class _FixtureResourceIndex:
    def __init__(self, public_items: list[dict], resolved: dict[str, dict]) -> None:
        self.public_items = public_items
        self.resolved = resolved
        self.get_calls: list[tuple[str, str]] = []
        self.read_calls: list[tuple[str, str]] = []

    def query(self, **_kwargs: object) -> dict:
        resources = []
        for item in self.public_items:
            resource_id = str(item.get("kb_id") or "")
            project = str(item.get("project") or "")
            resources.append({
                "resource_id": resource_id,
                "resource_type": str(item.get("type") or ""),
                "title": str(item.get("title") or resource_id),
                "summary": str(item.get("preview") or ""),
                "facets": ["knowledge"],
                "attributes": {
                    **item,
                    "project_ids": [project] if project else [],
                    "project_status": "active",
                },
                "source": {"adapter": "project_knowledge", "path": ""},
            })
        return {
            "schema": "tfi.portal.resource_index.v2",
            "index_updated_at": "2026-07-22T22:00:00+08:00",
            "resources": resources,
        }

    def projection_state(self, adapter: str) -> dict:
        if adapter == "project_knowledge":
            return {
                "allowed_types": ["thinking", "idea"],
                "projects": [{"id": "AlignMe", "status": "active"}],
            }
        return {}

    def status(self) -> dict:
        return {"running_adapters": []}

    def validate_id(self, resource_id: str) -> SimpleNamespace:
        for item in self.public_items:
            if str(item.get("kb_id") or "") == resource_id:
                return SimpleNamespace(resource_type=str(item.get("type") or ""))
        raise ValueError(f"invalid resource id: {resource_id}")

    def get(self, resource_id: str, *, actor: str = "") -> dict:
        self.get_calls.append((resource_id, actor))
        for record in self.query()["resources"]:
            if record["resource_id"] == resource_id:
                return record
        raise KeyError(resource_id)

    def read_content(
        self,
        address: str,
        *,
        actor: str = "",
    ) -> tuple[bytes, str, dict]:
        self.read_calls.append((address, actor))
        resolved = self.resolve(address, actor=actor)
        path = Path(str(resolved.get("resolved_path") or ""))
        return path.read_bytes(), "text/markdown; charset=utf-8", resolved

    def resolve(self, address: str, *, actor: str = "") -> dict:
        item = self.resolved[address]
        path = Path(str(item.get("selected_path") or item.get("path") or ""))
        detail = knowledge_source.public_item_record(item)
        record = next(
            value
            for value in self.query()["resources"]
            if value["resource_id"] == str(item.get("kb_id") or "")
        )
        return {
            "address": address,
            "root_id": str(item.get("kb_id") or ""),
            "record": record,
            "detail": detail,
            "content_kind": knowledge_source._file_kind(path),
            "filename": path.name,
            "resolved_path": str(path),
        }


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url) as response:
        assert response.status == 200
        return json.load(response)


@pytest.mark.parametrize("error,status", [
    (KeyError("missing resource"), 404),
    (PermissionError("access denied"), 403),
    (ValueError("invalid address"), 400),
])
def test_knowledge_detail_route_preserves_resource_errors(error, status):
    """知识详情路由把资源缺失、无权限和非法地址分别返回 404、403、400"""
    service = mock.Mock()
    service.resolve.side_effect = error
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with (
            mock.patch.object(http_auth, "_check_session", return_value=True),
            mock.patch.object(http_auth, "_get_session_username", return_value="reader"),
            mock.patch.object(serve_management_portal, "get_resource_service", return_value=service),
            mock.patch.object(web_resources, "get_resource_service", return_value=service),
            pytest.raises(urllib.error.HTTPError) as caught,
        ):
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/knowledge/api/detail?kb_id=thinking_0123456789ab"
            )
        assert caught.value.code == status
        assert json.loads(caught.value.read())["error"]
        service.resolve.assert_called_once_with("thinking_0123456789ab", actor="reader")
        service.read_content.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_knowledge_attachment_image_uses_authenticated_actor(tmp_path):
    """知识条目附件图片以登录用户身份解析并返回图片内容"""
    image = tmp_path / "figure.png"
    image.write_bytes(b"image-body")
    calls = []

    def resolve(raw_path, actor):
        calls.append((raw_path, actor))
        return image, "image/png"

    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with (
            mock.patch.object(http_auth, "_check_session", return_value=True),
            mock.patch.object(http_auth, "_get_session_username", return_value="reader"),
            mock.patch.object(
                serve_management_portal.knowledge_item_chat_backend.knowledge_item_chat_backend,
                "resolve_attachment_image", side_effect=resolve,
            ),
        ):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/knowledge/api/item-chat/attachment-image?path=figure.png"
            ) as response:
                assert response.status == 200
                assert response.read() == b"image-body"
        assert calls == [("figure.png", "reader")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_knowledge_archive_unavailable_keeps_service_error_response():
    """知识归档服务不可用时，创建归档请求返回 503 并带出服务名"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/knowledge/api/archive/create",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        unavailable = serve_management_portal.knowledge_archive_backend.KnowledgeArchiveServiceUnavailable
        with (
            mock.patch.object(http_auth, "_check_session", return_value=True),
            mock.patch.object(http_auth, "_get_session_username", return_value="reader"),
            mock.patch.object(
                serve_management_portal.knowledge_archive_backend.knowledge_archive_backend,
                "create_job", side_effect=unavailable("archive unavailable"),
            ),
            pytest.raises(urllib.error.HTTPError) as caught,
        ):
            urllib.request.urlopen(request)
        assert caught.value.code == 503
        response = json.loads(caught.value.read())
        assert response["service"] == "knowledge-archive-agent.service"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_knowledge_post_rejects_oversized_json_before_business_call():
    """知识库 POST 请求体超过上限时返回 413，不调用业务接口"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/knowledge/api/archive/create",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(http_request_body._JSON_REQUEST_MAX_BYTES + 1),
            },
            method="POST",
        )
        with (
            mock.patch.object(http_auth, "_check_session", return_value=True),
            mock.patch.object(
                serve_management_portal.knowledge_archive_backend.knowledge_archive_backend,
                "create_job",
            ) as create_job,
            pytest.raises(urllib.error.HTTPError) as caught,
        ):
            urllib.request.urlopen(request)
        assert caught.value.code == 413
        create_job.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("kind,filename", [("text", "notes.txt"), ("image", "figure.png")])
def test_knowledge_detail_nested_content_uses_authorized_resource_service(kind, filename):
    """知识详情的内嵌文件按读者授权经 ResourceService 解析，文本读出正文，图片只给原图链接"""
    address = f"thinking_0123456789ab/file/{filename}"
    resolution = {
        "address": address, "detail": {"kb_id": "thinking_0123456789ab"},
        "filename": filename, "content_kind": kind,
    }
    service = mock.Mock()
    service.resolve.return_value = resolution
    service.read_content.return_value = (b"nested content", "text/plain", resolution)
    detail = serve_management_portal.resource_views.knowledge_detail(address, service=service, actor="reader")
    service.resolve.assert_called_once_with(address, actor="reader")
    assert detail["address"] == address
    assert "%2Ffile%2F" in detail["download_url"]
    if kind == "text":
        service.read_content.assert_called_once_with(address, actor="reader")
        assert detail["text"] == "nested content"
    else:
        service.read_content.assert_not_called()
        assert detail["raw_url"].startswith("/api/resources/content?address=")
        assert detail["text"] == ""


def test_knowledge_graph_retains_actor_and_does_not_share_cached_read_access(tmp_path):
    """条目关系图按请求者身份解析，缓存不跨用户共享读权限，撤权后立即拒绝

    :importance: P2"""
    root = "thinking_0123456789ab"
    related = "idea_abcdef123456"
    root_path = tmp_path / "root.md"
    root_path.write_text(f"---\nlinks:\n  related:\n  - {related}\n---\nRoot", encoding="utf-8")
    related_path = tmp_path / "related.md"
    related_path.write_text("Related", encoding="utf-8")
    records = {
        root: {"kb_id": root, "path": str(root_path)},
        related: {"kb_id": related, "path": str(related_path)},
    }
    def resolve(resource_id, *, active_only=False, actor=""):
        assert actor in {"alice", "bob"}, "graph lost its authenticated actor"
        return dict(records[resource_id])
    def graph_records(actor=""):
        assert actor in {"alice", "bob"}
        return dict(records) if actor == "alice" else {root: records[root]}
    with knowledge_source._LOCK:
        knowledge_backend._ITEM_GRAPH_CACHE.clear()
    try:
        with (
            mock.patch.object(knowledge_backend, "resolve_item_record", side_effect=resolve) as resolver,
            mock.patch.object(knowledge_backend, "_item_graph_records", side_effect=graph_records),
        ):
            alice = knowledge_backend.build_item_graph(root, actor="alice")
            bob = knowledge_backend.build_item_graph(root, actor="bob")
            assert {node["kb_id"] for node in alice["nodes"]} == {root, related}
            assert {node["kb_id"] for node in bob["nodes"]} == {root}
            assert resolver.call_count == 2
            resolver.side_effect = PermissionError("root access revoked")
            with pytest.raises(PermissionError, match="revoked"):
                knowledge_backend.build_item_graph(root, actor="alice")
    finally:
        with knowledge_source._LOCK:
            knowledge_backend._ITEM_GRAPH_CACHE.clear()


def test_global_knowledge_graph_queries_and_caches_the_callers_view(tmp_path):
    """全局知识图谱按调用者的可见范围查询并分 actor 缓存，refresh 时重建"""
    root = "thinking_0123456789ab"
    related = "idea_abcdef123456"
    root_path = tmp_path / "root.md"
    related_path = tmp_path / "related.md"
    root_path.write_text(f"---\nlinks:\n  related:\n  - {related}\n---\nRoot", encoding="utf-8")
    related_path.write_text("Related", encoding="utf-8")
    paths = {root: root_path, related: related_path}
    visible = {"alice": [root, related], "bob": [root]}

    def index(*, actor="", **kwargs):
        return {"items": [{"kb_id": item_id} for item_id in visible.get(actor, [])]}

    def query(*, actor="", **kwargs):
        return {"resources": [
            {"resource_id": item_id, "source": {"path": str(paths[item_id])}}
            for item_id in visible.get(actor, [])
        ]}

    service = mock.Mock()
    service.query.side_effect = query
    with (
        mock.patch.dict(knowledge_backend._KNOWLEDGE_GRAPH_CACHE, clear=True),
        mock.patch.object(knowledge_backend, "resource_index_snapshot", side_effect=index) as snapshot,
        mock.patch.object(knowledge_backend, "get_resource_service", return_value=service),
        mock.patch.object(knowledge_source, "get_resource_service", return_value=service),
        mock.patch.object(knowledge_details, "get_resource_service", return_value=service),
    ):
        alice = knowledge_backend.build_knowledge_graph(actor="alice")
        bob = knowledge_backend.build_knowledge_graph(actor="bob")
        assert {node["kb_id"] for node in alice["nodes"]} == {root, related}
        assert alice["edges"] == [{"source": root, "target": related, "relation": "related"}]
        assert {node["kb_id"] for node in bob["nodes"]} == {root}
        assert bob["edges"] == []
        assert [call.kwargs["actor"] for call in snapshot.call_args_list] == ["alice", "bob"]
        assert [call.kwargs["actor"] for call in service.query.call_args_list] == ["alice", "bob"]
        assert knowledge_backend.build_knowledge_graph(actor="alice") == alice
        assert snapshot.call_count == 2

        visible["alice"] = [root]
        refreshed = knowledge_backend.build_knowledge_graph(actor="alice", refresh=True)
        assert {node["kb_id"] for node in refreshed["nodes"]} == {root}
        assert refreshed["edges"] == []
        with pytest.raises(ValueError, match="actor required"):
            knowledge_backend.build_knowledge_graph(actor="")


def test_knowledge_card_click_http_chain_accepts_public_records_without_file_id(
    tmp_path: Path,
) -> None:
    """公开记录不含 file_id 时，点击知识卡片后的详情、图谱、对话和讨论接口都能打开"""
    root_id = "thinking_0123456789ab"
    related_id = "idea_abcdef123456"
    root_path = tmp_path / "root.md"
    related_path = tmp_path / "related.md"
    root_path.write_text(
        f"---\nkb_id: {root_id}\ntype: thinking\nproject: AlignMe\nlinks:\n"
        f"  related:\n    - {related_id}\n---\n\nRoot body\n",
        encoding="utf-8",
    )
    related_path.write_text(
        f"---\nkb_id: {related_id}\ntype: idea\nproject: AlignMe\nlinks:\n"
        "  related: []\n---\n\nRelated body\n",
        encoding="utf-8",
    )
    resolved = {
        root_id: {
            "kb_id": root_id,
            "address": root_id,
            "type": "thinking",
            "title": "Root item",
            "project": "AlignMe",
            "path": str(root_path),
            "selected_path": str(root_path),
            "files": [{
                "file_id": "0123456789abcdef",
                "name": root_path.name,
                "relative_path": root_path.name,
                "kind": "markdown",
                "primary": True,
            }],
        },
        related_id: {
            "kb_id": related_id,
            "address": related_id,
            "type": "idea",
            "title": "Related item",
            "project": "AlignMe",
            "path": str(related_path),
            "selected_path": str(related_path),
            "files": [{
                "file_id": "fedcba9876543210",
                "name": related_path.name,
                "relative_path": related_path.name,
                "kind": "markdown",
                "primary": True,
            }],
        },
    }
    public_items = [
        {
            key: value
            for key, value in knowledge_source.public_item_record(item).items()
            if key != "files"
        }
        for item in resolved.values()
    ]
    assert all("file_id" not in item and "path" not in item for item in public_items)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    index = _FixtureResourceIndex(public_items, resolved)
    try:
        with knowledge_source._LOCK:
            knowledge_backend._ITEM_GRAPH_CACHE.clear()
        with (
            mock.patch.object(http_auth, "_check_session", return_value=True),
            mock.patch.object(http_auth, "_get_session_username", return_value="playwright"),
            mock.patch.object(
                serve_management_portal,
                "get_resource_service",
                return_value=index,
            ),
            mock.patch.object(
                web_resources,
                "get_resource_service",
                return_value=index,
            ),
            mock.patch.object(web_resources, "_require_resource"),
            mock.patch.object(
                knowledge_backend,
                "resolve_item_address",
                side_effect=lambda address, active_only=False: dict(resolved[address]),
            ),
            mock.patch.object(
                knowledge_backend,
                "resolve_item_record",
                side_effect=lambda kb_id, active_only=False, actor="": dict(resolved[kb_id]),
            ),
                mock.patch.object(
                    knowledge_backend,
                    "_item_graph_records",
                    return_value=resolved,
                ),
            mock.patch.object(
                knowledge_backend,
                "build_knowledge_graph",
                return_value={
                    "schema": "tfi.knowledge_graph.v1",
                    "nodes": public_items,
                    "edges": [{
                        "source": root_id,
                        "target": related_id,
                        "relation": "related",
                    }],
                    "relation_counts": {"related": 1},
                    "unresolved_links": 0,
                },
            ) as build_knowledge_graph,
            mock.patch.object(
                serve_management_portal.knowledge_item_chat_backend.knowledge_item_chat_backend,
                "get_state",
                return_value={
                    "kb_id": root_id,
                    "messages": [],
                    "pending_job": None,
                    "context_options": {"skills": [], "agents": []},
                },
            ),
        ):
            listing = _get_json(
                f"{base}/knowledge/api/index?active_only=0&offset=0&limit=200"
            )
            clicked_id = listing["items"][0]["kb_id"]
            assert clicked_id == root_id
            assert "file_id" not in listing["items"][0]

            item = _get_json(f"{base}/knowledge/api/item?kb_id={clicked_id}")
            detail = _get_json(f"{base}/knowledge/api/detail?kb_id={clicked_id}")
            assert index.read_calls[-1] == (clicked_id, "playwright")
            graph = _get_json(f"{base}/knowledge/api/item-graph?kb_id={clicked_id}")
            global_graph = _get_json(f"{base}/knowledge/api/graph")
            chat = _get_json(f"{base}/knowledge/api/item-chat?kb_id={clicked_id}")
            assert detail["kb_id"] == root_id
            assert item["kb_id"] == root_id
            assert index.get_calls == [(root_id, "playwright")]
            assert detail["address"] == root_id
            assert "id" not in detail
            assert detail["text"].endswith("Root body\n")
            assert {node["kb_id"] for node in graph["nodes"]} == {root_id, related_id}
            assert global_graph["schema"] == "tfi.knowledge_graph.v1"
            assert global_graph["edges"][0]["relation"] == "related"
            build_knowledge_graph.assert_called_once_with(refresh=False, actor="playwright")
            assert chat["kb_id"] == root_id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

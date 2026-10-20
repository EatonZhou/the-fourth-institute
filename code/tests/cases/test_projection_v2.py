import pytest

from management_portal.workbench_projections.store import Store, ProjectionError, validate_manifest
from management_portal.workbench_projections import semantics


APP = "workbench_111111111111"


def test_region_chat_binding_is_checked_and_resolved_independently(monkeypatch):
    """分栏对话校验真实绑定，不能把请求退回主门面。"""
    from types import SimpleNamespace
    from management_portal.workbench_projections import http
    value = page()
    value["bindings"]["release"] = {"lineage_id": "lineage_111111111111", "residents": {"facade": "resident_222222222222"}}
    value["manifest"]["sidebar"][1]["chat"] = {"binding": "release", "role": "facade"}
    validate_manifest(value)
    checked = []
    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(
        store=SimpleNamespace(get=lambda *args, **kwargs: value),
        check_bindings=lambda bindings, actor: checked.append((bindings, actor)),
    ))
    result = http.post("/api/workbenches/facade", {"id": APP, "panel": "progress"}, "alice")
    assert result["resident_id"] == "resident_222222222222"
    assert checked == [({"release": value["bindings"]["release"]}, "alice")]
    with pytest.raises(ProjectionError):
        http.post("/api/workbenches/facade", {"id": APP, "panel": "unknown"}, "alice")
    value["manifest"]["sidebar"][1]["chat"]["binding"] = "missing"
    with pytest.raises(ProjectionError):
        validate_manifest(value)


def page():
    return {
        "title": "开发投影", "html": "",
        "bindings": {"main": {"lineage_id": "lineage_111111111111", "residents": {"facade": "resident_111111111111"}}},
        "manifest": {"schema": "tfi.projection.v2", "chat": {"binding": "main", "role": "facade"},
                     "sidebar": [{"id": "architecture", "title": "架构", "source": "panels/a.html"}, {"id": "progress", "title": "进展", "source": "panels/p.html"}],
                     "context": [{"channel": "focus", "publishers": ["architecture"], "subscribers": ["progress"]}]},
        "panels": {"panels/a.html": "<p>架构第一版</p>", "panels/p.html": "<p>进展</p>"},
    }


def test_page_package_publication_and_panel_preferences():
    """页面包发布区分草稿版本并隔离用户面板偏好"""
    store = Store()
    initial = store.create(APP, page(), "alice")
    store.publish(APP, "alice", 1)
    panels = {**initial["panels"], "panels/a.html": "<p>架构草稿</p>"}
    store.update(APP, {"panels": panels}, "alice", 1)
    assert store.get(APP, "alice", published=True)["panels"] == initial["panels"]
    assert store.get(APP, "alice")["panels"] == panels
    store.publish(APP, "alice", 2)
    assert store.get(APP, "alice", published=True)["manifest"] == initial["manifest"]
    assert store.get(APP, "alice", published=True)["panels"] == panels
    store.state(APP, "alice", {"draft": "保留"}, panel="architecture")
    store.state(APP, "alice", {"watermark": "now"}, panel="progress")
    assert store.state(APP, "alice", panel="architecture") == {"draft": "保留"}
    assert store.state(APP, "bob", panel="architecture") == {}


def test_invalid_packages_cannot_be_saved(subtests):
    """无效投影页面包在保存前被拒绝"""
    invalid = []
    value = page(); value["manifest"]["sidebar"][1]["id"] = "architecture"; invalid.append(value)
    value = page(); value["manifest"]["sidebar"][0]["source"] = "../secret.html"; invalid.append(value)
    value = page(); value["panels"]["panels/a.html"] = "<nav>独立导航</nav>"; invalid.append(value)
    value = page(); value["manifest"]["context"][0]["publishers"] = ["missing"]; invalid.append(value)
    value = page(); value["bindings"]["main"]["residents"] = {}; invalid.append(value)
    value = page(); value["html"] = "<p>双重源码</p>"; invalid.append(value)
    value = page(); del value["panels"]["panels/a.html"]; invalid.append(value)
    for value in invalid:
        with subtests.test(value=value), pytest.raises(ProjectionError):
            validate_manifest(value)


def test_v1_keeps_single_source_round_trip():
    """第一版投影发布后保留单一 HTML 源码"""
    store = Store()
    store.create(APP, {"title": "v1", "html": "<p>保留</p>"}, "alice")
    store.publish(APP, "alice", 1)
    app = store.get(APP, "alice", published=True)
    assert "manifest" not in app
    assert app["html"] == "<p>保留</p>"


def test_semantic_references_require_current_actor_access(monkeypatch):
    """语义引用按当前调用者授权且已验证状态需提供依据"""
    calls = []
    class Resources:
        def resolve(self, ref, *, actor):
            calls.append((ref, actor))
            if ref == "decision_222222222222":
                raise PermissionError("forbidden")
            return {"root_id": ref.split("/")[0], "exists": True}
    monkeypatch.setattr(semantics, "get_resource_service", Resources)
    obj = {"ref": "decision_111111111111", "kind": "design", "status": "proposed", "source": "", "observed_at": "2026-10-15T00:00:00Z"}
    assert semantics.semantic_object(obj, "alice") == obj
    assert calls == [(obj["ref"], "alice")]
    with pytest.raises(PermissionError):
        semantics.semantic_object({**obj, "source": "decision_222222222222"}, "bob")
    assert calls[-1][1] == "bob"
    with pytest.raises(ProjectionError, match="依据"):
        semantics.semantic_object({**obj, "status": "verified"}, "alice")
    semantics.semantic_object({**obj, "ref": obj["ref"] + "/file/example.py#L12"}, "alice")
    assert calls[-1][0] == obj["ref"] + "/file/example.py"


def test_observe_keeps_per_request_limit_and_authorizes_every_batch(monkeypatch):
    """投影观察每次请求限制 128 条且每批重新授权"""
    from types import SimpleNamespace
    from management_portal.workbench_projections import http

    calls = []
    grants = {"alice", "bob"}
    denied = set()

    def get_app(app_id, actor, *, published):
        assert app_id == "workbench_111111111111"
        assert published is False
        if actor not in grants:
            raise PermissionError("workbench forbidden")
        return {}

    class Resources:
        def resolve(self, ref, *, actor):
            calls.append((ref, actor))
            if ref in denied:
                raise PermissionError("resource forbidden")
            return {"root_id": ref.split("/")[0], "exists": True}

    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=SimpleNamespace(get=get_app)))
    monkeypatch.setattr(semantics, "get_resource_service", Resources)
    refs = [f"workspace_{index:012x}/file/source.json#L1" for index in range(584)]

    def observe(batch, actor="alice"):
        return http.post("/api/workbenches/observe", {
            "id": "workbench_111111111111", "preview": True, "refs": batch,
            "actor": "owner",  # Caller-supplied identity never authorizes a batch.
        }, actor)

    with pytest.raises(ProjectionError, match="128"):
        observe(refs)
    assert calls == []
    observed = []
    for offset in range(0, len(refs), 128):
        observed += observe(refs[offset:offset + 128])["references"]
    assert [item["ref"] for item in observed] == refs
    assert calls == [(ref.removesuffix("#L1"), "alice") for ref in refs]
    denied.add(refs[300].removesuffix("#L1"))
    with pytest.raises(PermissionError, match="resource forbidden"):
        observe(refs[256:384], "bob")
    assert calls[-1] == (refs[300].removesuffix("#L1"), "bob")
    count = len(calls)
    grants.remove("alice")
    with pytest.raises(PermissionError, match="workbench forbidden"):
        observe(refs[:128])
    assert len(calls) == count


def test_projection_resolves_evidence_as_current_actor(monkeypatch, subtests):
    """投影按当前调用者解析证据且隐藏私有元数据"""
    from types import SimpleNamespace
    from management_portal.workbench_projections import http
    from management_portal import resources
    from management_portal.resources.errors import ResourceOperationError, ResourceServiceUnavailable

    calls = []

    def get_app(app_id, actor):
        calls.append((app_id, actor))
        if actor == "outsider":
            raise PermissionError("workbench forbidden")

    metadata = {
        "exists": True, "resource_type": "workspace", "title": "evidence.json",
        "portal_url": "/api/resources/content?address=evidence",
        "record": {"title": "Workspace", "attributes": {"path": "/private/workspace"}},
        "resolved_path": "/private/workspace/evidence.json", "content": "private body",
        "metadata": {"owner_ids": ["alice"]}, "download_url": "/private-download",
    }
    failures = {
        "private": PermissionError("private object details"),
        "missing": KeyError("missing object details"),
        "missing-file": FileNotFoundError("/private/absent.json"),
        "domain-private": ResourceOperationError("resource.forbidden", "private", 403),
        "domain-missing": ResourceOperationError("resource.missing", "missing", 404),
        "unavailable": ResourceServiceUnavailable("temporarily unavailable"),
        "invalid": ValueError("invalid address"),
    }

    def resolve(address, *, actor):
        calls.append((address, actor))
        if address in failures:
            raise failures[address]
        if address == "deleted":
            return {**metadata, "exists": False}
        if address == "root":
            return {key: value for key, value in metadata.items() if key != "title"}
        return metadata

    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=SimpleNamespace(get=get_app)))
    monkeypatch.setattr(resources, "get_resource_service", lambda: SimpleNamespace(resolve=resolve))
    payload = {"id": APP, "operation": "resolve", "arguments": {"address": "evidence"}}
    expected = {
        "readable": True, "resource_type": "workspace", "title": "evidence.json",
        "portal_url": metadata["portal_url"],
    }
    assert http.post("/api/workbenches/resource", payload, "alice") == expected
    assert calls == [(APP, "alice"), ("evidence", "alice")]
    assert http.post("/api/workbenches/resource", {**payload, "arguments": {"address": "root"}}, "alice") == {**expected, "title": "Workspace"}
    for address in ["private", "missing", "missing-file", "domain-private", "domain-missing", "deleted"]:
        with subtests.test(address=address):
            result = http.post("/api/workbenches/resource", {**payload, "arguments": {"address": address}}, "alice")
            assert {key: result[key] for key in ("readable", "resource_type", "title", "portal_url")} == {
                "readable": False, "resource_type": "", "title": "", "portal_url": "",
            }
            assert result["status"] == (403 if address in ("private", "domain-private") else 404)
            assert result["error"]["code"]
            assert result["error"]["details"] == {}
            assert result["error"]["error"] == ("资源不可访问" if result["status"] == 403 else "资源不存在")
            assert calls[-1] == (address, "alice")
    for address in ["unavailable", "invalid"]:
        with subtests.test(address=address), pytest.raises(type(failures[address])):
            http.post("/api/workbenches/resource", {**payload, "arguments": {"address": address}}, "alice")
    for name in ["actor", "principal_id", "registry_root", "automatic"]:
        with subtests.test(name=name), pytest.raises(PermissionError):
            http.post("/api/workbenches/resource", {**payload, "arguments": {"address": "evidence", name: "bob"}}, "alice")
        assert calls[-1] == (APP, "alice")
    with pytest.raises(PermissionError, match="workbench forbidden"):
        http.post("/api/workbenches/resource", payload, "outsider")
    assert calls[-1] == (APP, "outsider")

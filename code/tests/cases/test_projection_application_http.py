"""Package requests bind bytes to an authenticated immutable revision.

:module: workbench.panels
:importance: P1
:design: decision_7a5ae487e2e9
"""
import sys
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from management_portal.workbench_projections import application_http, http
from management_portal.workbench_projections.store import ProjectionError


APP = "workbench_111111111111"
DIGEST = "a" * 64
BASE = f"{application_http.PREFIX}{APP}/3/{DIGEST}/"


@pytest.fixture
def assets(monkeypatch):
    calls = []
    build = {"status": "ready", "artifact_hash": DIGEST,
             "files": {"entry.html": {"sha256": "b" * 64}, "chunks/main.js": {"sha256": "c" * 64}}}

    def get_revision(app_id, actor, revision):
        calls.append((app_id, actor, revision))
        if actor != "alice":
            raise PermissionError("not readable")
        if revision != 3:
            raise ProjectionError("projection.revision", "unknown revision", 404)
        return {"application_build": build}

    def read_artifact(receipt, path):
        calls.append((receipt, path))
        return b"<html><head></head><body>package</body></html>", "text/html"

    monkeypatch.setitem(sys.modules, "management_portal.workbench_projections.application_build", SimpleNamespace(read_artifact=read_artifact))
    store = SimpleNamespace(get_revision=get_revision)
    return store, build, calls


def test_asset_requires_current_actor_and_exact_revision(assets):
    """应用包资源要求当前身份与精确修订鉴权

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store, build, calls = assets
    content, content_type, headers = application_http.read_asset(BASE + "entry.html", "alice", store)
    assert calls == [(APP, "alice", 3), (build, "entry.html")]
    assert content_type == "text/html" and b"package" in content
    assert headers["Content-Security-Policy"] == "sandbox; default-src 'none'; frame-ancestors 'none'"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["ETag"] == '"' + "b" * 64 + '"'
    assert not any(name.startswith("Access-Control") for name in headers)
    with pytest.raises(PermissionError):
        application_http.read_asset(BASE + "entry.html", "bob", store)
    assert calls[-1] == (APP, "bob", 3)
    for url in [BASE.replace("/3/", "/2/") + "entry.html", BASE.replace(DIGEST, "d" * 64) + "entry.html", BASE + "other.js"]:
        with pytest.raises(ProjectionError):
            application_http.read_asset(url, "alice", store)
        assert calls[-1][0] == APP  # Rejected before any bytes were read.
    build["status"] = "failed"
    with pytest.raises(ProjectionError):
        application_http.read_asset(BASE + "entry.html", "alice", store)


def test_asset_paths_do_not_escape_or_use_internal_storage(assets, subtests):
    """应用包资源路径禁止越界与暴露内部存储

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store, _, calls = assets
    for path in ["../entry.html", "%2e%2e/entry.html", "%252e%252e/entry.html", "/entry.html", "chunks//main.js", "chunks/./main.js", "chunks%5cmain.js", "entry.html%00", "entry.html?actor=alice", "%ff"]:
        with subtests.test(path=path), pytest.raises(ProjectionError):
            application_http.read_asset(BASE + path, "alice", store)
    assert calls == []
    application_http.read_asset(BASE + "chunks/main.js", "alice", store)
    assert calls[-1][1] == "chunks/main.js"


def test_asset_route_serves_exact_bytes_without_portal_shell(monkeypatch, assets):
    """应用包路由返回原始资源字节且不注入宿主页面

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    from management_portal import serve_management_portal as web

    store, _, _ = assets
    sent = []
    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(web, "_serve_payload", lambda *args, **kwargs: sent.append((args, kwargs)))
    monkeypatch.setattr(web, "_serve_bytes", lambda *args, **kwargs: pytest.fail("must not inject privileged Portal bootstrap"))
    handler = object()
    http.handle(handler, urlparse(BASE + "entry.html"), "alice")
    args, kwargs = sent[0]
    assert args[:2] == (handler, 200)
    assert args[2] == b"<html><head></head><body>package</body></html>"
    assert kwargs["cache_control"] == "private, no-store"


def test_library_routes_authorize_revision_and_only_read_registered_files(monkeypatch, assets):
    """登记库路由按修订鉴权并限定登记文件

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    from management_portal import serve_management_portal as web

    store, build, calls = assets
    base = f"{application_http.LIBRARY_PREFIX}{APP}/3"
    entry = "d3/7.9.0/d3.min.js"
    catalog = {"d3": {"version": "7.9.0", "entry": entry, "files": [{"path": entry, "sha256": "d" * 64}], "licenses": []}}
    reads, sent = [], []

    def read_library(path):
        reads.append(path)
        return b"window.d3={};", "text/javascript"

    monkeypatch.setitem(sys.modules, "management_portal.workbench_projections.libraries", SimpleNamespace(registry=lambda: catalog, read_library=read_library))
    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(web, "_serve_json", lambda *args, **kwargs: sent.append((args, kwargs)))
    monkeypatch.setattr(web, "_serve_payload", lambda *args, **kwargs: sent.append((args, kwargs)))
    handler = object()
    http.handle(handler, urlparse(base), "alice")
    assert calls[-1] == (APP, "alice", 3)
    assert sent.pop()[0] == (handler, 200, catalog)
    http.handle(handler, urlparse(base + "/" + entry), "alice")
    assert reads == [entry] and calls[-1] == (APP, "alice", 3)
    args, kwargs = sent.pop()
    assert args == (handler, 200, b"window.d3={};", "text/javascript")
    assert kwargs["extra_headers"]["ETag"] == '"' + "d" * 64 + '"'
    assert kwargs["cache_control"] == "private, no-store"
    with pytest.raises(PermissionError):
        application_http.serve_library(handler, base + "/" + entry, "bob", store)
    for target in [base.replace("/3", "/2"), base + "/../../projection_host.js", base + "/d3/7.9.0/unknown.js", base + "/%ff"]:
        with pytest.raises(ProjectionError):
            application_http.serve_library(handler, target, "alice", store)
    build["status"] = "failed"
    with pytest.raises(ProjectionError):
        application_http.serve_library(handler, base, "alice", store)
    assert reads == [entry]

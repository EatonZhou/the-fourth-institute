"""
:module: workbench.panels
:importance: P1
:design: decision_7a5ae487e2e9
"""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from management_portal.workbench_projections import libraries
from management_portal.resources import ResourceOperationError


ROOT = Path(__file__).resolve().parents[2]


def test_registered_libraries_are_pinned_and_every_asset_is_verified():
    """投影登记库版本固定且全部资源校验完整性

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    catalog = libraries.registry()
    assert {name: item["version"] for name, item in catalog.items()} == {
        "d3": "7.9.0", "dagre": "1.1.5", "cytoscape": "3.33.1",
        "marked": "18.1.1", "dompurify": "3.4.17", "portal-graph": "1.0.0",
    }
    for library in catalog.values():
        assert library["licenses"]
        for item in library["files"]:
            content, content_type = libraries.read_library(item["path"])
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            assert len(content) == item["size"]
            assert content_type == item["content_type"]
    # Lineage and projections use the same reviewed D3 distribution.
    original = (ROOT / "management_portal/static/vendor/d3/d3.v7.9.0.min.js").read_bytes()
    assert libraries.read_library(catalog["d3"]["entry"])[0] == original


def test_library_read_rejects_noncanonical_and_unregistered_paths():
    """投影库读取拒绝非规范与未登记路径

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    for path in ["../projection_sdk.js", "/etc/passwd", "d3/../d3/7.9.0/d3.min.js",
                 "d3//7.9.0/d3.min.js", "d3/7.9.0/./d3.min.js", "d3\\7.9.0\\d3.min.js",
                 "manifest.json", "sync.py", "unknown/1.0/file.js"]:
        with pytest.raises(ResourceOperationError) as raised:
            libraries.read_library(path)
        assert raised.value.code in {"projection.library_path", "projection.library_not_found"}


def test_library_read_fails_closed_on_corrupt_or_symlinked_bytes(tmp_path, monkeypatch):
    """投影库内容损坏或符号链接时拒绝读取

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = libraries._ROOT
    catalog = libraries.registry()
    item = catalog["portal-graph"]["files"][0]
    relative = item["path"]
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema": "tfi.projection-libraries.v1", "libraries": {"portal-graph": catalog["portal-graph"]},
    }))
    monkeypatch.setattr(libraries, "_ROOT", tmp_path)
    target.write_bytes(b"tampered")
    with pytest.raises(ResourceOperationError, match="摘要不匹配"):
        libraries.read_library(relative)
    target.unlink()
    target.symlink_to(source / relative)
    with pytest.raises(ResourceOperationError, match="符号链接"):
        libraries.read_library(relative)


def test_shared_lineage_geometry_contract():
    """共享 Lineage 几何计算的 Node 契约验证

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(["node", "--test", "tests/cases/fixtures/projection_geometry.test.cjs"],
                            cwd=ROOT, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

    # Exercise the production router and static handler, not a generic fixture
    # file server: nested assets must retain their path and stay allowlisted.
    import threading
    import urllib.request
    import re
    from http.server import ThreadingHTTPServer
    from management_portal import serve_management_portal as portal

    class QuietHandler(portal.Handler):
        def log_message(self, _format, *_args):
            pass

    entry = libraries.registry()["portal-graph"]["entry"]
    asset_path = "/static/projection_libraries/" + entry
    routes = portal._portal_route_definitions()
    exposed = [route for route in routes if route.path.startswith("/static/projection_libraries/")]
    assert [(route.method, route.path, route.match, route.phase) for route in exposed] == [
        ("GET", asset_path, "exact", "public")]
    for blocked in ["/static/projection_libraries/manifest.json",
                    "/static/projection_libraries/d3/7.9.0/d3.min.js",
                    asset_path.replace("1.0.0", "2.0.0"), asset_path + "/extra",
                    asset_path.replace("/geometry.js", "/../geometry.js"),
                    asset_path.replace("/geometry.js", "/%2e%2e/geometry.js")]:
        assert not any(route.matches("GET", blocked, "public") for route in routes), blocked

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}{asset_path}?v=fixture"
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == "text/javascript"
            assert response.read() == libraries.read_library(entry)[0]
            assert response.headers["ETag"]
        # A generic static fixture can hide a missing production allowlist
        # entry by serving its bytes anyway. Keep the real lazy-load chain live.
        loader = (ROOT / "management_portal/static/portal_hatg_loader.js").read_text()
        dependencies = set(re.findall(r'["\'](/static/[^"\']+\.(?:js|css))["\']', loader))
        assert asset_path in dependencies
        for dependency in sorted(dependencies):
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{dependency}", timeout=10) as response:
                assert response.status == 200
                expected_type = "text/css" if dependency.endswith(".css") else "text/javascript"
                assert response.headers.get_content_type() == expected_type, dependency
                assert response.read() == (ROOT / "management_portal" / dependency.lstrip("/")).read_bytes(), dependency
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

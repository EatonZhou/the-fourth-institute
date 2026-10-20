from __future__ import annotations

import shutil
import subprocess
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse, urlencode

import pytest

from management_portal import resource_auto_registration, resource_identity_backend, repo_workspace_backend
from management_portal.resources import ResourceService
from management_portal.resources.providers import install_mutation_providers
from management_portal.resources.providers.repo import install_resolver
from management_portal.resources.registry_store import get_registry_store
from management_portal.http_repo import RepoHttp
from tests.cases.registry_support import write_projects


def _source(tmp_path: Path, **overrides):
    root = tmp_path / "exported source"
    root.mkdir()
    (root / "architecture").mkdir()
    (root / "architecture" / "modules.yaml").write_text("modules: []\n")
    (root / "README.txt").write_text("Exported source documentation\n")
    record = {
        "schema": "tfi.repo.v1", "id": "repo_0123456789ab", "type": "repo",
        "name": "Example", "status": "active", "source_kind": "exported_tree",
        "canonical_checkout": str(root), "checkouts": [{"path": str(root)}],
        "project_ids": [], "remotes": [],
        **overrides,
    }
    _save(record)
    write_projects({"projects": []})
    return record, root


def _save(record):
    with get_registry_store().writer("repo") as writer:
        writer.put("repo", record["id"], record)


def _service():
    index = mock.Mock()
    index.change_targets.return_value = ["resource_registry"]
    index.queue_change.return_value = ["resource_registry"]
    return ResourceService(index_provider=index, resolver_loader=install_resolver,
                           mutation_provider_loader=install_mutation_providers)


def _workspace_http():
    return RepoHttp(
        session_actor=lambda _request: "test-reader",
        resource_service=_service,
        read_json=lambda request: request,
        require_resource=lambda repo_id, _actor: resource_identity_backend.get_repo_record(repo_id),
        notify_index=lambda **_kwargs: None,
        respond=lambda _request, status, body: (status, body),
        exception_response=lambda _request, exc: (_ for _ in ()).throw(exc),
    )


@pytest.mark.parametrize("git_location", [None, "root", "parent"])
def test_exported_workspace_http_browses_source_without_git(tmp_path, monkeypatch, git_location):
    """工作台三个真实HTTP处理入口读源码，邻近Git不产生历史或写能力。"""
    record, root = _source(tmp_path)
    if git_location:
        subprocess.run(["git", "init", "-q", str(root if git_location == "root" else tmp_path)], check=True)
    monkeypatch.setattr(repo_workspace_backend, "_git_bytes", lambda *_a, **_k: pytest.fail("exported browse invoked Git"))
    http = _workspace_http()
    def get(action="", path=""):
        return http.workspace_get(None, urlparse("/?" + urlencode({"path": path})), "", {"repo_id": record["id"], "action": action})
    status, overview = get()
    assert status == 200
    assert overview["source_kind"] == "exported_tree"
    assert overview["capabilities"] == {"browse_files": True, "git": False, "write_files": False, "search_files": False, "statistics": False}
    assert overview["checkout_path"] == str(root)
    assert overview["head"] is None and overview["status"] is None
    assert not overview["remotes"] and not overview["checkout_writable"]
    status, tree = get("tree")
    assert status == 200
    assert [item["name"] for item in tree["entries"]] == ["architecture", "README.txt"]
    assert tree["readme"]["content"] == "Exported source documentation\n"
    assert tree["head"] is None and tree["status"] is None
    status, tree = get("tree", "architecture")
    assert status == 200 and tree["entries"][0]["path"] == "architecture/modules.yaml"
    status, file = get("file", "architecture/modules.yaml")
    assert status == 200 and file["content"] == "modules: []\n"
    assert not file["editable"] and file["last_commit"] is None
    assert get("file", "missing.txt")[0] == 404
    # Storage remains canonical even when a stale alternative checkout exists.
    record["checkouts"] = [{"path": str(tmp_path)}]
    _save(record)
    assert get()[1]["checkout_path"] == str(root)
    shutil.rmtree(root)
    assert get()[0] == 404


def test_exported_workspace_http_rejects_git_and_writes(tmp_path):
    """只读能力覆盖HTTP读写路由，不能借浏览入口启用Git或文件变更。"""
    record, root = _source(tmp_path)
    http = _workspace_http()
    for action in ("commits", "commit", "blame", "diff", "credentials", "stats", "search"):
        status, result = http.workspace_get(None, urlparse("/?path=README.txt&sha=" + "a" * 40 + "&q=read"), "", {"repo_id": record["id"], "action": action})
        assert status == 400 and "exported_tree" in result["error"], (action, status, result)
    payloads = {
        "file": {"path": "README.txt", "content": "changed"},
        "entry": {"path": "new.txt", "operation": "create"},
        "move": {"path": "README.txt", "destination": "moved.txt"},
        "commit": {"message": "Must reject"},
        "auto-commit": {},
        "branch": {"operation": "create", "name": "test"},
        "sync": {"operation": "fetch"},
        "discard": {},
        "credentials": {"operation": "delete", "credential_id": "test"},
    }
    for action, payload in payloads.items():
        status, result = http.workspace_post(payload, urlparse("/"), "", {"repo_id": record["id"], "action": action})
        assert status == 400 and "exported_tree" in result["error"], (action, status, result)
    assert (root / "README.txt").read_text() == "Exported source documentation\n"
    assert not (root / "new.txt").exists() and not (root / ".git").exists()


def test_exported_workspace_paths_do_not_expose_outside_or_git_metadata(tmp_path):
    """目录、文件和README都不沿逃逸链接读取内容，也拒绝Git目录别名。"""
    record, root = _source(tmp_path)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret").write_text("private bytes")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "linked.txt").symlink_to(outside / "secret")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("metadata")
    (root / "metadata").symlink_to(root / ".git", target_is_directory=True)
    (root / "README.txt").unlink()
    (root / "README.txt").symlink_to(outside / "secret")
    http = _workspace_http()
    for action in ("tree", "file"):
        for path in ("../private/secret", str(outside), "escape/secret", ".git/config", "metadata/config", "a\\b", "bad\x00name"):
            status, _ = http.workspace_get(None, urlparse("/?" + urlencode({"path": path})), "", {"repo_id": record["id"], "action": action})
            assert status == 400, (action, path, status)
    tree = repo_workspace_backend.list_repo_tree(record["id"])
    assert tree["readme"] is None
    assert ".git" not in {item["name"] for item in tree["entries"]}
    link = repo_workspace_backend.read_repo_file(record["id"], "linked.txt")
    assert link["kind"] == "symlink" and link["content"] == "" and not link["editable"]


@pytest.mark.skipif(os.environ.get("PORTAL_TEST_REPO_BROWSER") != "1", reason="explicit Repo browser check requires installed TAU Playwright/Chromium")
def test_exported_workspace_browser_navigation(tmp_path):
    """隔离Chromium用实际Repo控制器与HTTP后端浏览文件并拒绝Git标签直链。"""
    record, root = _source(tmp_path)
    git_root = tmp_path / "git"
    git_root.mkdir()
    (git_root / "README.txt").write_text("Git fixture\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(git_root)], check=True)
    subprocess.run(["git", "-C", str(git_root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(git_root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Initial"], check=True)
    git_record = resource_identity_backend.register_repo_checkout(git_root)
    repo_root = Path(__file__).resolve().parents[2]
    http = _workspace_http()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            match = re.fullmatch(r"/api/repos/(repo_[0-9a-f]{12})/workspace(?:/(\w+))?", parsed.path)
            if match:
                status, data = http.workspace_get(None, parsed, "", {"repo_id": match[1], "action": match[2] or ""})
            elif parsed.path == "/api/resource-index/query":
                status, data = 200, {"resources": [{"resource_id": item["id"], "title": item["name"], "summary": "Fixture source"} for item in (record, git_record)]}
            elif parsed.path == "/api/repos/automation/status":
                status, data = 200, {"repos": []}
            elif parsed.path.startswith("/static/"):
                target = (repo_root / "management_portal" / parsed.path.lstrip("/")).resolve()
                if target.is_relative_to(repo_root / "management_portal/static") and target.is_file():
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript" if target.suffix == ".js" else "text/css")
                    self.end_headers()
                    self.wfile.write(target.read_bytes())
                    return
                status, data = 404, {}
            else:
                status, data = 404, {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = subprocess.run(["node", str(repo_root / "tests/cases/javascript/exported_repo_workspace.cjs"), f"http://127.0.0.1:{server.server_port}", record["id"], git_record["id"]], text=True, capture_output=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_exported_files_survive_reconcile_and_remain_readable(tmp_path):
    """无 Git 的显式源码树经过自动核对后仍保留原 ID 并可读取投影 YAML。"""
    record, root = _source(tmp_path)
    service = _service()
    registrar = resource_auto_registration.ResourceAutoRegistrar(service=service)
    for _ in range(2):
        result = registrar.reconcile()
        assert result["removed"] == []
        assert result["resource_errors"] == []
        assert get_registry_store().get("repo", record["id"]) == record
        content, _mime, _resolved = service.read_content(
            record["id"] + "/file/architecture/modules.yaml", actor="resident_0123456789ab")
        assert content == b"modules: []\n"
        content, mime, _resolved = service.read_content(
            record["id"] + "/file/README.txt", actor="resident_0123456789ab")
        assert content == b"Exported source documentation\n"
        assert mime == "text/plain"
    assert not (root / ".git").exists()
    with pytest.raises(PermissionError):
        service.read_content(record["id"] + "/file/architecture/modules.yaml")


@pytest.mark.parametrize("failure", ["missing", "file"])
def test_exported_tree_without_its_root_is_unregistered(tmp_path, failure):
    """导出类型不会使已失效的 canonical 根目录永久存活。"""
    record, root = _source(tmp_path)
    shutil.rmtree(root)
    if failure == "file":
        root.write_text("not a directory")
    result = resource_auto_registration.ResourceAutoRegistrar(service=_service()).reconcile()
    assert result["removed"] == [record["id"]]
    assert not result["resource_errors"]
    if failure == "file":
        assert root.read_text() == "not a directory"


@pytest.mark.parametrize("kind", [None, "git_checkout"])
def test_plain_repo_does_not_gain_exported_tree_liveness(tmp_path, kind):
    """普通 Git 记录仍在 .git 缺失时注销，不能按目录存活。"""
    record, _root = _source(tmp_path)
    if kind is None:
        del record["source_kind"]
    else:
        record["source_kind"] = kind
    _save(record)
    result = resource_auto_registration.ResourceAutoRegistrar(service=_service()).reconcile()
    assert result["removed"] == [record["id"]]


@pytest.mark.parametrize("overrides", [
    {"source_kind": "unknown"}, {"source_kind": ""},
    {"canonical_checkout": ""}, {"canonical_checkout": "relative/path"},
])
def test_malformed_source_binding_is_rejected_without_unregistering(tmp_path, overrides):
    """契约损坏须拒绝读取并保留原记录供修复，不猜测成另一种绑定。"""
    record, _root = _source(tmp_path, **overrides)
    with pytest.raises(ValueError):
        resource_identity_backend.resolve_repo_address(record["id"])
    result = resource_auto_registration.ResourceAutoRegistrar(service=_service()).reconcile()
    assert result["removed"] == []
    assert result["resource_errors"][0]["resource_id"] == record["id"]
    assert get_registry_store().get("repo", record["id"]) == record


def test_exported_source_does_not_fall_back_or_escape_canonical_root(tmp_path):
    """旧 checkout 和符号链接均不能把导出文件请求引到 canonical 根之外。"""
    record, root = _source(tmp_path)
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "secret").write_text("private")
    record["checkouts"] = [{"path": str(outside)}]
    _save(record)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    for suffix in ("../other/secret", "escape/secret"):
        with pytest.raises(ValueError):
            resource_identity_backend.resolve_repo_address(record["id"] + "/file/" + suffix)
    shutil.rmtree(root)
    resolved = resource_identity_backend.resolve_repo_address(record["id"])
    assert resolved["checkout_path"] == str(root)
    assert not resolved["exists"]
    result = resource_auto_registration.ResourceAutoRegistrar(service=_service()).reconcile()
    assert result["removed"] == [record["id"]]
    assert (outside / "secret").read_text() == "private"


def test_exported_tree_permission_failure_is_reported_without_unregistering(tmp_path, monkeypatch):
    """权限错误不是失效证明，保留注册记录并报告。"""
    record, root = _source(tmp_path)
    original = Path.stat
    def inaccessible(path, *args, **kwargs):
        if path == root:
            raise PermissionError("permission denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", inaccessible)
    result = resource_auto_registration.ResourceAutoRegistrar(service=_service()).reconcile()
    assert result["removed"] == []
    assert result["resource_errors"][0]["resource_id"] == record["id"]
    assert get_registry_store().get("repo", record["id"]) == record


def test_git_discovery_cannot_reclassify_an_exported_tree(tmp_path):
    """发现 Git 元数据不能偷偷改变导出身份，也不能按旧远端关联到别的仓库。"""
    record, root = _source(tmp_path)
    origin = "https://example.invalid/shared-origin.git"
    record["remotes"] = [{"name": "origin", "is_identity": True, "normalized_url": origin}]
    _save(record)
    other = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "remote", "add", "origin", origin], check=True)
    registered = resource_identity_backend.register_repo_checkout(other)
    assert registered["id"] != record["id"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for operation in (
        lambda: resource_identity_backend.register_repo_checkout(root),
        lambda: resource_identity_backend.scan_and_sync_repo_registry([root], apply=True),
    ):
        with pytest.raises(ValueError, match="exported"):
            operation()
    assert get_registry_store().get("repo", record["id"]) == record
    write_projects({"projects": [{"id": "ExistingProject", "path": str(root)}]})
    result = resource_auto_registration.ResourceAutoRegistrar(service=_service()).reconcile()
    assert not result["removed"] and not result["registered"] and not result["resource_errors"]
    assert get_registry_store().get("repo", record["id"]) == record


@pytest.mark.parametrize("git_location", ["root", "parent"])
def test_exported_tree_refuses_git_history_and_mutation_even_with_git_nearby(tmp_path, git_location):
    """导出身份不能借用本地或祖先仓库的历史和写能力。"""
    record, root = _source(tmp_path)
    git_root = root if git_location == "root" else tmp_path
    subprocess.run(["git", "init", "-q", str(git_root)], check=True)
    subprocess.run(["git", "-C", str(git_root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(git_root), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "Fixture"], check=True)
    sha = subprocess.check_output(["git", "-C", str(git_root), "rev-parse", "HEAD"], text=True).strip()
    relative = "architecture/modules.yaml" if git_location == "root" else "exported source/architecture/modules.yaml"
    operations = [
        lambda: resource_identity_backend.resolve_repo_address(record["id"] + "/commit/" + sha + "/file/" + relative),
        lambda: resource_identity_backend.update_repo_remote(record["id"], name="origin", url="https://example.invalid/repo.git"),
        lambda: repo_workspace_backend.list_repo_commits(record["id"]),
        lambda: repo_workspace_backend.write_repo_file(record["id"], "new.txt", "must not write"),
    ]
    before = (git_root / ".git" / "config").read_bytes()
    for operation in operations:
        with pytest.raises(ValueError, match="exported"):
            operation()
    assert (git_root / ".git" / "config").read_bytes() == before
    assert not (root / "new.txt").exists()
    assert get_registry_store().get("repo", record["id"]) == record

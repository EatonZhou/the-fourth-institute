"""Host folder selection lists real directories, retaining path authorization."""
from types import SimpleNamespace

import pytest

from management_portal.workbench_projections import host_directories, http


def test_navigation_unregistered_folders_links_and_pagination(tmp_path, monkeypatch):
    calls = []
    def authorize(path, actor):
        calls.append((str(path), actor))
        if path.name == "private":
            raise PermissionError("denied")
    monkeypatch.setattr(host_directories, "authorize", authorize)
    # A directory of its own: the site fixture keeps account registration locks under tmp_path.
    root = tmp_path / "picker"
    root.mkdir()
    (root / "发行目录").mkdir()
    (root / "empty").mkdir()
    (root / "private").mkdir()
    (root / "notes.txt").write_text("not returned")
    (root / "link").symlink_to(root / "发行目录", target_is_directory=True)
    (root / "hidden-link").symlink_to(root / "private", target_is_directory=True)
    monkeypatch.setattr(host_directories, "PAGE_SIZE", 2)
    first = host_directories.browse({"path": str(root)}, "alice")
    second = host_directories.browse({"path": str(root), "after": first["next_after"]}, "alice")
    assert [entry["name"] for entry in first["directories"] + second["directories"]] == ["empty", "link", "发行目录"]
    assert first["parent"] == str(root.parent)
    opened = host_directories.browse({"path": str(root / "link")}, "alice")
    assert opened["path"] == str(root / "发行目录")
    assert opened["directories"] == []
    assert calls and all(actor == "alice" for _, actor in calls)
    with pytest.raises(PermissionError):
        host_directories.browse({"path": str(root / "hidden-link")}, "alice")


def test_path_owner_and_existing_read_permission(monkeypatch, tmp_path):
    from management_portal.society_service import lineage_access_policy as policy
    calls = []
    monkeypatch.setattr(policy, "path_resource", lambda path: (path, "workspace_private", "bob"))
    monkeypatch.setattr(policy, "file_reaches", lambda actor, resource, **kw: calls.append((actor, resource, kw)) or False)
    with pytest.raises(PermissionError):
        host_directories.authorize(tmp_path, "alice")
    assert calls == [("alice", "workspace_private", {"write": False, "owner": "bob"})]
    monkeypatch.setattr(policy, "path_resource", lambda path: None)
    monkeypatch.setattr(policy, "file_reaches", lambda actor, resource, **kw: calls.append((actor, resource, kw)) or True)
    host_directories.authorize(tmp_path, "alice")
    assert calls[-1] == ("alice", str(tmp_path), {"write": False, "owner": ""})


def test_http_uses_session_actor_and_checks_workbench(tmp_path, monkeypatch):
    calls = []
    def get(app, actor):
        if app != "workbench_test" or actor != "alice":
            raise PermissionError("workbench denied")
        calls.append((app, actor))
    monkeypatch.setattr(http, "get_service", lambda: SimpleNamespace(store=SimpleNamespace(get=get)))
    monkeypatch.setattr(host_directories, "authorize", lambda path, actor: None)
    payload = {"id": "workbench_test", "arguments": {"path": str(tmp_path)}}
    assert http.post("/api/workbenches/directories", payload, "alice")["path"] == str(tmp_path)
    assert len(calls) == 2
    with pytest.raises(PermissionError):
        http.post("/api/workbenches/directories", payload, "bob")
    for args in ({"path": str(tmp_path), "actor": "alice"}, {"path": "relative"}, {"path": "\x00"}):
        with pytest.raises(ValueError):
            host_directories.browse(args, "alice")


def test_directory_disappears_and_permission_revoked(tmp_path, monkeypatch):
    attempts = []
    def authorize(path, actor):
        if path == tmp_path:
            attempts.append(path)
            if len(attempts) > 1:
                raise PermissionError("revoked")
    monkeypatch.setattr(host_directories, "authorize", authorize)
    with pytest.raises(PermissionError, match="revoked"):
        host_directories.browse({"path": str(tmp_path)}, "alice")
    with pytest.raises(FileNotFoundError):
        host_directories.browse({"path": str(tmp_path / "missing")}, "alice")

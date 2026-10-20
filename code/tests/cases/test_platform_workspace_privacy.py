"""Native path spelling must not bypass personal Workspace authorization."""
from contextlib import nullcontext
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest

from management_portal.resources.providers import workspace
from management_portal.society_service import lineage_access_policy as policy


class WindowsPath(PureWindowsPath):
    # Pure geometry only; junction/symlink handling remains the native Path contract.
    def resolve(self, strict=False):
        return self


@pytest.mark.parametrize("root", [r"C:\Portal Data\personal", r"\\server\share\personal"])
def test_windows_workspace_checks_full_native_path_before_read(monkeypatch, root):
    monkeypatch.setattr(workspace, "Path", WindowsPath)
    record = {"attributes": {"path": root}}
    service = SimpleNamespace(get=lambda *_a, **_k: record)
    checked = []
    monkeypatch.setattr(policy, "workspace_reaches", lambda *a, **kw: checked.append(kw) or False)
    monkeypatch.setattr(workspace.folder_files, "describe", lambda *_a, **_k: pytest.fail("denied file touched"))
    with pytest.raises(KeyError):
        workspace._resolve(service, "workspace_demo/file/private.txt", actor="alice")
    assert checked == [{"write": False, "path": str(WindowsPath(root) / "private.txt")}]


@pytest.mark.parametrize("root", ["C:relative", r"\root-relative", "relative", "C:\\", r"C:\data\..\other"])
def test_windows_workspace_rejects_ambiguous_registered_root(monkeypatch, root):
    monkeypatch.setattr(workspace, "Path", WindowsPath)
    with pytest.raises(workspace.ResourceOperationError):
        workspace._root({"attributes": {"path": root}})


@pytest.mark.parametrize("path, inside", [
    (r"C:\Portal Data\personal\secret.txt", True),
    (r"c:\portal data\PERSONAL\secret.txt", True),
    (r"C:\Portal Data\personal-other\secret.txt", False),
    (r"D:\Portal Data\personal\secret.txt", False),
    (r"C:personal\secret.txt", False),
])
def test_windows_personal_area_uses_native_component_boundary(monkeypatch, path, inside):
    import pathlib
    folders = [(r"C:\Portal Data\personal", "lineage_private", "owner")]
    with monkeypatch.context() as patch:
        patch.setattr(pathlib, "Path", WindowsPath)
        assert policy.personal_area(path, folders) == (folders[0] if inside else None)


def test_unc_personal_area(monkeypatch):
    import pathlib
    area = (r"\\host\share\personal", "lineage_private", "owner")
    with monkeypatch.context() as patch:
        patch.setattr(pathlib, "Path", WindowsPath)
        assert policy.personal_area(r"\\HOST\share\personal\child", [area]) == area
        assert policy.personal_area(r"\\host\other\personal\child", [area]) is None


def test_nested_windows_workspace_keeps_personal_owner(monkeypatch):
    import pathlib
    from management_portal.resources import workspace_store
    from management_portal.society_service import state_store

    rows = [{"path": r"C:\private\nested", "workspace_id": "workspace_nested"}]
    connection = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(fetchall=lambda: rows))
    monkeypatch.setattr(state_store, "get_lineage_data_store", lambda: SimpleNamespace(database=SimpleNamespace(read=lambda: nullcontext(connection))))
    monkeypatch.setattr(workspace_store, "get_workspace_store", lambda: SimpleNamespace(table="workspaces"))
    monkeypatch.setattr(policy, "personal_folders", lambda: [(r"C:\private", "lineage_private", "owner")])
    with monkeypatch.context() as patch:
        patch.setattr(pathlib, "Path", WindowsPath)
        assert policy.path_resource(r"C:\private\nested\secret") == (r"C:\private\nested", "workspace_nested", "owner")


def test_native_personal_area_follows_links_and_component_boundaries(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    folders = [(str(private), "lineage_private", "owner")]
    assert policy.personal_area(str(private / "secret"), folders) == folders[0]
    assert policy.personal_area(str(tmp_path / "private-other" / "secret"), folders) is None
    link = tmp_path / "link"
    try:
        link.symlink_to(private, target_is_directory=True)
    except OSError:
        pytest.skip("native symbolic link privilege unavailable")
    assert policy.personal_area(str(link / "secret"), folders) == folders[0]


def test_windows_folder_removal_preserves_acl_and_propagates_denial(tmp_path, monkeypatch):
    from management_portal.society_service import folder_removal
    root = tmp_path / "owned"
    root.mkdir()
    (root / "result").write_text("owned")
    native_os = folder_removal.os
    monkeypatch.setattr(folder_removal, "os", SimpleNamespace(
        name="nt", walk=native_os.walk, path=native_os.path,
        lstat=lambda *_: pytest.fail("POSIX owner check on Windows"),
        chmod=lambda *_: pytest.fail("ACL must not be widened")))
    with monkeypatch.context() as patch:
        def denied(*_args):
            raise PermissionError("ACL denies deletion")
        patch.setattr(folder_removal.shutil, "rmtree", denied)
        with pytest.raises(PermissionError, match="ACL"):
            folder_removal.remove_tree(root)
        assert root.exists()
    folder_removal.remove_tree(root)
    assert not root.exists()

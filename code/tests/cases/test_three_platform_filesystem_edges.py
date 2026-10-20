"""Native path syntax must not change root containment or backup selection."""
from pathlib import Path, PureWindowsPath

import pytest

from management_common import skill_paths
from management_portal import resource_backup_backend


def test_windows_skill_file_path_uses_native_separator(monkeypatch):
    class WindowsPath(PureWindowsPath):
        def expanduser(self):
            return self
        def resolve(self, **kwargs):
            return self
        def is_file(self):
            return str(self) == r"D:\Skills\example-skill\SKILL.md"
    monkeypatch.setattr(skill_paths, "Path", WindowsPath)
    path = r"D:\Skills\example-skill\SKILL.md"
    assert skill_paths.normalize_additional_skills([path], skill_root=r"D:\Skills") == [path]
    with pytest.raises(ValueError):
        skill_paths.normalize_additional_skills([r"E:\Skills\example-skill\SKILL.md"], skill_root=r"D:\Skills")


def test_skill_symlink_cannot_escape_root(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("outside")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        skill_paths.normalize_additional_skills([str(root / "escape" / "SKILL.md")], skill_root=str(root))


@pytest.mark.parametrize("value", [r"C:\Backups", r"\\server\share\Backups"])
def test_windows_backup_target_is_an_absolute_native_path(monkeypatch, value):
    monkeypatch.setattr(resource_backup_backend, "Path", PureWindowsPath)
    # The selector is pure: it must not touch a live backup repository.
    owner = next(value for value in vars(resource_backup_backend).values()
                 if isinstance(value, type) and "_target_path" in vars(value))
    assert owner._target_path({"path": value}, {}) == value
    assert owner._target_path({"path": r"C:relative"}, {}) == ""
    assert owner._target_path({"path": r"\relative"}, {}) == ""
@pytest.mark.parametrize("root", ["C:/work/project", r"\\server\share\project"])
def test_workspace_save_allows_uses_native_absolute_root(monkeypatch, root):
    import sys
    from types import SimpleNamespace
    from management_portal.resources import isa

    calls = []
    monkeypatch.setattr(isa, "Path", PureWindowsPath)
    monkeypatch.setitem(sys.modules, "management_portal.society_service.lineage_access_policy",
                        SimpleNamespace(workspace_reaches=lambda *a, **kw: calls.append((a, kw)) or True))
    result = isa._save_allows({"resource_type": "workspace", "root_id": "workspace_example",
                              "relative_path": "nested/file.md", "record": {"attributes": {"path": root}}}, "resident_actual")
    assert result == {"allows": {"UpdateWorkspaceFile": True}}
    assert calls == [(("resident_actual", "workspace_example"), {"write": True, "path": str(PureWindowsPath(root) / "nested/file.md")})]

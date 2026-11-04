"""DeleteProject: a project and everything in it (decision_6591e7793116, plan decision 9)."""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager

import pytest
import yaml

from management_portal.society_service import project_domain
from management_portal.project_seeds import project_seed_documents
from tests.cases.registry_support import ProjectRegistryYaml, write_projects


def _configure_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *,
                       lineages: tuple[list[str], list[str]] = ([], [])):
    projects_root = tmp_path / "Projects"
    project_root = projects_root / "Example"
    project_root.mkdir(parents=True)
    for relative, content in project_seed_documents("Example", "test project").items():
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (project_root / "notes.md").write_text("Business content that goes with the project.\n", encoding="utf-8")
    registry = ProjectRegistryYaml()
    write_projects({"schema_version": 1, "projects": [
        {"id": "Example", "path": str(project_root), "description": "test project",
         "directory": {"owner_id": "Example"}},
    ]})
    archive = tmp_path / "archive" / "project_deletions.json"
    deleted: list[str] = []

    from management_portal.society_service import lineage_lifecycle

    def delete_lineage(lineage_id, actor):
        deleted.append(lineage_id)
        return {"deleted_resident_ids": [lineage_id.replace("lineage_", "resident_")]}

    monkeypatch.setattr(project_domain, "PROJECTS_ROOT", projects_root)
    monkeypatch.setattr(project_domain, "_project_lineage_ids", lambda identities: lineages)
    @contextmanager
    def prepare(lineage_id, actor):
        yield lambda: delete_lineage(lineage_id, actor)
    monkeypatch.setattr(lineage_lifecycle, "prepare_lineage_deletion", prepare)
    return project_root, registry, archive, deleted


def test_a_project_with_content_is_deleted_with_its_lineages_residents_and_files(monkeypatch, tmp_path):
    """删除项目同时清除所属 Lineage、Resident 和文件"""
    ordinary, principal = ["lineage_aaaaaaaaaaaa", "lineage_bbbbbbbbbbbb"], ["lineage_cccccccccccc"]
    project_root, registry, archive, deleted = _configure_project(
        monkeypatch, tmp_path, lineages=(ordinary, principal),
    )
    result = project_domain.delete_project("Example", "alice")
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    assert project_root.exists()
    get_directory_cleanup_store().run_pending()
    # Ordinary Lineages first, the Project-level Lineage last.
    assert deleted == [*ordinary, *principal]
    assert result["lineage_ids"] == [*ordinary, *principal]
    assert result["resident_ids"] == [item.replace("lineage_", "resident_") for item in [*ordinary, *principal]]
    # Files are deleted directly; only the audit record remains.
    assert not project_root.exists()
    assert "deleted_path" not in result
    assert yaml.safe_load(registry.read_text(encoding="utf-8"))["projects"] == []
    from management_portal.resources.registry_store import get_registry_store
    entry = get_registry_store().get('project_deletions', 'Example')
    assert entry['project_id'] == 'Example'
    assert entry['lineage_ids'] == [*ordinary, *principal]


def test_unknown_projects_and_anonymous_callers_are_refused(monkeypatch, tmp_path):
    """删除项目拒绝未知项目和匿名调用者"""
    _configure_project(monkeypatch, tmp_path)
    with pytest.raises(KeyError):
        project_domain.delete_project("Missing", "alice")
    with pytest.raises(ValueError):
        project_domain.delete_project("Example", "")


def test_created_project_deletion_cascades_external_bindings_before_file_cleanup(monkeypatch, tmp_path):
    """A surviving reader cannot retain an owner's deleted directory or registration."""
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.society_service.state_store import LineageDataStore
    root, _, _, _ = _configure_project(monkeypatch, tmp_path)
    workspaces = get_workspace_store()
    workspaces.ensure('workspace_cccccccccccc', str(root))
    reader = tmp_path / 'reader'
    reader.mkdir()
    workspaces.ensure('workspace_bbbbbbbbbbbb', str(reader))
    store = LineageDataStore()
    with store.database.transaction() as connection:
        connection.execute('INSERT INTO residents(resident_id,title,description,owned_workspace_id) VALUES (%s,%s,%s,%s)',
                           ('resident_cccccccccccc', 'Reader', 'Reads the project', 'workspace_bbbbbbbbbbbb'))
        connection.execute('INSERT INTO resident_workspace_bindings VALUES (%s,%s,%s)',
                           ('resident_cccccccccccc', 'workspace_cccccccccccc', 'project files'))

    project_domain.delete_project('Example', 'alice')

    assert root.exists()
    assert not workspaces.rows(['workspace_cccccccccccc'])
    assert workspaces.rows(['workspace_bbbbbbbbbbbb'])
    with store.database.read() as connection:
        assert not connection.execute('SELECT 1 FROM resident_workspace_bindings').fetchone()
        assert connection.execute('SELECT deleted_at FROM residents WHERE resident_id=%s',
                                  ('resident_cccccccccccc',)).fetchone()['deleted_at'] is None
    get_directory_cleanup_store().run_pending()
    assert not root.exists()
    assert reader.is_dir()

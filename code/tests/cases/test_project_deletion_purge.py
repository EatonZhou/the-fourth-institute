"""DeleteProject removes everything without archives and can be finished by a retry
(decision_4648f7fa3295, decision_6591e7793116)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import lineage_lifecycle, project_domain, resident_lifecycle_steps_domain
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.resources import isa as resource_isa
from tests.cases.test_project_deletion import _configure_project


def test_the_archive_step_of_a_project_deletion_removes_the_resident_folder(tmp_path):
    """项目删除直接移除 Resident 目录且不创建归档

    :module: spec.design-governance"""
    folder = tmp_path / "residents" / "resident_aaaaaaaaaaaa"
    (folder / "workspace").mkdir(parents=True)
    (folder / "resident.md").write_text("identity\n", encoding="utf-8")
    # Removing the folder keeps live Residents' Workspaces, so it reads the Lineage tables.
    get_lineage_data_store()

    resident_lifecycle_steps_domain.apply_resident_lifecycle_step("operation", {"kind": "remove", "source": str(folder)})

    assert not folder.exists()
    assert not (tmp_path / "archive").exists()


def test_already_moved_lineage_deletion_journal_can_finish(monkeypatch, tmp_path):
    """新清理流程能完成旧版已经移动目录的持久删除任务

    :module: spec.design-governance"""
    deleted_root = tmp_path / "deleted"
    target = deleted_root / "lineage_aaaaaaaaaaaa_lineage-delete_20261001"
    (target / "residents").mkdir(parents=True)
    monkeypatch.setattr(lineage_lifecycle, "_deleted_root", lambda: deleted_root)

    intent = {"lineage_id": "lineage_aaaaaaaaaaaa", "target_path": str(target),
              "source_path": str(tmp_path / 'original'), "operation_id": 'lineage-delete_20261001',
              "deleted_at": '2026-10-19T00:00:00Z', "deleted_by": 'alice'}
    proof = json.dumps({key: intent[key] for key in (
        'operation_id', 'lineage_id', 'source_path', 'target_path',
    )}, sort_keys=True, separators=(',', ':'))
    (target / lineage_lifecycle.LINEAGE_DELETION_MARKER).write_text(proof)
    lineage_lifecycle._purge_deleted_lineage_folder(intent)
    assert not target.exists()

    outside = tmp_path / "elsewhere" / "lineage_aaaaaaaaaaaa_lineage-delete_20261001"
    outside.mkdir(parents=True)
    with pytest.raises(ValueError):
        lineage_lifecycle._purge_deleted_lineage_folder({**intent, "target_path": str(outside)})
    assert outside.exists()


class _ResourceService:
    def __init__(self, resources, *, failing=()):
        self.resources, self.failing, self.unregistered = resources, set(failing), []

    def query(self, *, actor, projects, limit, offset=0, include_attributes=False):
        return {"resources": self.resources[offset:offset + limit], "total": len(self.resources)}

    def unregister(self, resource_id, *, actor, resource_type=""):
        if resource_id in self.failing:
            raise PermissionError("source unavailable")
        self.unregistered.append(resource_id)


def _delete_with(monkeypatch, service, *, validate=None, papers=None, project_record=None):
    domain_calls = []

    def delete_project(project_id, actor):
        domain_calls.append(list(service.unregistered))
        return {"lineage_ids": ["lineage_aaaaaaaaaaaa"], "resident_ids": [], "project_path": "/share/Projects/Example"}

    from management_portal import paper_store

    monkeypatch.setattr(paper_store, "PAPER_STORE", papers or SimpleNamespace(replace_project=lambda *args, **kwargs: []))
    monkeypatch.setattr(resource_isa, "_service", lambda: service)
    monkeypatch.setattr(resource_isa, "_project_record",
                        project_record or (lambda project_id, actor: {"resource_id": "Example"}))
    from management_portal import society_service
    monkeypatch.setattr(society_service, "get_society_service",
                        lambda: SimpleNamespace(project=SimpleNamespace(
                            delete_project=delete_project,
                            validate_project_deletion=validate or (lambda *args: {"deleted": False}),
                        )))
    invocation = SimpleNamespace(caller="alice", argument=lambda name, default=None: {"project_id": "Example"}.get(name, default))
    return resource_isa.delete_project(invocation), domain_calls


RESOURCES = [
    {"resource_id": "Example", "resource_type": "project"},
    {"resource_id": "knowledge_aaaaaaaaaaaa", "resource_type": "knowledge"},
    {"resource_id": "repo_aaaaaaaaaaaa", "resource_type": "repo"},
    {"resource_id": "lineage_aaaaaaaaaaaa", "resource_type": "lineage"},
    {"resource_id": "resident_aaaaaaaaaaaa", "resource_type": "resident"},
]


def test_unsafe_project_path_is_rejected_before_unregistering_resources(monkeypatch, tmp_path):
    """项目路径为符号链接时在资源注销前拒绝删除"""
    root, registry, _archive, deleted = _configure_project(monkeypatch, tmp_path)
    alias = root.parent / "Alias"
    alias.symlink_to(root, target_is_directory=True)
    document = yaml.safe_load(registry.read_text())
    document["projects"][0]["path"] = str(alias)
    registry.write_text(yaml.safe_dump(document))
    service = _ResourceService(RESOURCES)
    with pytest.raises(ValueError, match="symbolic"):
        _delete_with(monkeypatch, service, validate=project_domain.validate_project_deletion)
    assert service.unregistered == [] and deleted == []
    assert root.exists() and alias.is_symlink()


def test_independent_workspace_does_not_block_project_deletion_entry(monkeypatch, tmp_path):
    """独立 Workspace 不在项目删除入口额外拒绝已授权操作"""
    from management_portal.resources.workspace_store import get_workspace_store

    root, _registry, _archive, deleted = _configure_project(monkeypatch, tmp_path)
    (root / "src").mkdir()
    get_workspace_store().ensure("workspace_dddddddddddd", str(root / "src"))
    service = _ResourceService(RESOURCES)
    unlinked = []
    papers = SimpleNamespace(replace_project=lambda *args, **kwargs: unlinked.append(args) or [])
    _delete_with(monkeypatch, service, validate=project_domain.validate_project_deletion, papers=papers)
    assert service.unregistered == ['knowledge_aaaaaaaaaaaa', 'repo_aaaaaaaaaaaa', 'Example']


def test_project_resources_are_unregistered_before_the_project_content_is_deleted(monkeypatch):
    """项目内容删除前先注销资源且项目记录最后注销

    :module: spec.design-governance"""
    from management_portal import permission_system
    from management_portal.resources.workspace_store import get_workspace_store

    workspaces = get_workspace_store()
    for workspace_id, path in (("workspace_aaaaaaaaaaaa", "/share/Projects/Example/src"),
                               ("workspace_bbbbbbbbbbbb", "/share/Projects/Other/src")):
        workspaces.ensure(workspace_id, path)
        workspaces.database.write(lambda connection, workspace_id=workspace_id: workspaces.mark_deleted(
            workspace_id, deleted_at="2026-10-17T00:00:00Z", deleted_by="alice", connection=connection))
    retired = []
    monkeypatch.setattr(permission_system, "retire_resources", lambda ids, **_: retired.append(list(ids)) or [])
    service = _ResourceService(RESOURCES)
    result, domain_calls = _delete_with(monkeypatch, service)

    # Lineages and Residents are removed by Lineage deletion; the project record goes last.
    assert domain_calls == [["knowledge_aaaaaaaaaaaa", "repo_aaaaaaaaaaaa"]]
    assert service.unregistered == ["knowledge_aaaaaaaaaaaa", "repo_aaaaaaaaaaaa", "Example"]
    assert result["resource_ids"] == ["knowledge_aaaaaaaaaaaa", "repo_aaaaaaaaaaaa", "Example"]
    # The Workspaces retired with the directory leave the permission trees too.
    assert retired == [["workspace_aaaaaaaaaaaa"]]


def test_a_resource_that_cannot_be_unregistered_stops_before_any_content_is_deleted(monkeypatch):
    """资源注销失败时停止删除并报告已注销条目

    :module: spec.design-governance"""
    service = _ResourceService(RESOURCES, failing={"repo_aaaaaaaaaaaa"})
    with pytest.raises(InstructionError) as error:
        _delete_with(monkeypatch, service)

    assert error.value.code == "instruction.failed"
    assert [item["resource_id"] for item in error.value.details["failed"]] == ["repo_aaaaaaaaaaaa"]
    assert error.value.details["unregistered_resource_ids"] == ["knowledge_aaaaaaaaaaaa"]


def test_retiring_the_project_record_last_is_not_silently_skipped(monkeypatch):
    """项目记录最终注销失败时向调用者报告错误"""
    # The last step had no project unregistration and its error was dropped,
    # so FastRobotDemo's authorization resource stayed active after deletion.
    service = _ResourceService(RESOURCES, failing={"Example"})
    with pytest.raises(PermissionError):
        _delete_with(monkeypatch, service)


def test_calling_again_after_the_index_dropped_the_project_finishes_the_deletion(monkeypatch):
    """项目已移出索引后重试仍能完成注销"""
    # FastRobotDemo was deleted before its record could be retired; the index
    # no longer listed it, so DeleteProject answered "not found" and could not finish.
    from management_portal.hatg.contracts import not_found

    def gone(project_id, actor):
        raise not_found(f"Project 不存在：{project_id}")

    service = _ResourceService([])
    result, _calls = _delete_with(monkeypatch, service, validate=lambda *args: {"deleted": True}, project_record=gone)
    assert service.unregistered == ["Example"] and result["resource_ids"] == ["Example"]

    def never_existed(project_id, actor):
        raise KeyError("project not found")

    with pytest.raises(InstructionError) as error:
        _delete_with(monkeypatch, service, validate=never_existed, project_record=gone)
    assert error.value.code == "instruction.not_found"


def test_the_project_record_is_unregistered_only_after_deleteproject_removed_it(monkeypatch):
    """项目记录仅在 DeleteProject 删除项目后允许注销"""
    from management_portal.resources.providers import project as provider

    providers = {}
    provider.install_mutation_provider(SimpleNamespace(
        register_mutation_provider=lambda resource_type, **handlers: providers.update({resource_type: handlers})))
    monkeypatch.setattr(provider, "load_projects_payload", lambda: {"projects": [{"id": "Example"}]})
    unregister = providers["project"]["unregister"]
    with pytest.raises(ValueError, match="DeleteProject"):
        unregister("Example", actor="alice")
    assert unregister("Gone", actor="alice") == {"resource_id": "Gone", "status": "deleted"}


def test_papers_related_to_a_deleted_project_lose_the_relation_and_stay_in_the_library(monkeypatch, tmp_path):
    """删除项目解除论文关联并保留论文库条目"""
    # FastRobotDemo could not be deleted: its related papers were unregistered
    # like project content, and papers have no unregistration.
    from management_portal import paper_store

    manifests = {}
    for name, projects in {"only": ["Example"], "shared": ["Example", "Other"], "other": ["Other"]}.items():
        path = tmp_path / "items/2026" / name / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"paper_id": f"paper_{name}", "relations": {
            "projects": [{"project_id": item, "relation": "relevant"} for item in projects]}}))
        manifests[name] = path
    service = _ResourceService([*RESOURCES, {"resource_id": "paper_shared", "resource_type": "paper"}])
    result, _calls = _delete_with(monkeypatch, service, papers=paper_store.PaperStore(tmp_path))

    assert "paper_shared" not in service.unregistered
    assert sorted(result["unlinked"]["paper"]) == ["paper_only", "paper_shared"]
    projects = {name: [item["project_id"] for item in json.loads(path.read_text())["relations"]["projects"]]
                for name, path in manifests.items()}
    assert projects == {"only": [], "shared": ["Other"], "other": ["Other"]}

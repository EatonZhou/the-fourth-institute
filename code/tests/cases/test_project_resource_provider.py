from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from management_portal.portal_access_control import PortalAccessStore
from management_portal import project_registry
from management_portal.resources.providers import project
from tests.cases.registry_support import put_registry_document, write_projects


class FakeAccessStore:
    def __init__(self) -> None:
        self.users = {
            "alice": {
                "username": "alice",
                "configured": True,
                "status": "active",
            }
        }
        self.updates: list[dict[str, Any]] = []

    def user(self, username: str) -> dict[str, Any]:
        return dict(
            self.users.get(
                username,
                {
                    "username": username,
                    "configured": False,
                    "status": "active",
                },
            )
        )



def test_resident_project_creation_needs_no_human_administrator(monkeypatch):
    """Resident 创建项目无需人类管理员"""
    access = FakeAccessStore()
    monkeypatch.setattr(project, 'create_project', lambda payload, **_context: {'resource_id': payload['id']})
    result = project.register_project({'id': 'NewProject'}, actor='resident_0123456789ab', access_store=access)
    assert result == {'resource_id': 'NewProject'}
    assert access.updates == []


def test_resident_project_creation_preserves_accounts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resident 创建项目保留用户账号状态"""
    write_projects("schema_version: 1\nprojects: []\n")
    access = PortalAccessStore()
    access.upsert_user("alice", actor="test", status="active")
    monkeypatch.setattr(project, "SHARE_ROOT", tmp_path)
    monkeypatch.setattr(project, "PROJECTS_ROOT", tmp_path / "Projects")

    result = project.register_project(
        {
            "id": "NewProject",
            "project_type": "company",
        },
        actor="resident_0123456789ab",
        access_store=access,
    )

    assert result["resource_id"] == "NewProject"
    assert "memberships" not in access.user("alice")
    assert (tmp_path / "Projects" / "NewProject" / "summary.md").is_file()


def test_project_registry_requires_the_current_explicit_schema() -> None:
    """项目注册表要求显式声明当前结构版本"""
    put_registry_document(project_registry.COLLECTION, project_registry.DOCUMENT_ID, {"version": 1, "projects": []})

    with pytest.raises(RuntimeError, match="requires schema_version 1"):
        project.load_projects_payload()


def test_user_project_creation_does_not_write_project_roles(monkeypatch):
    """用户创建项目不写入项目角色"""
    access = FakeAccessStore()
    monkeypatch.setattr(project, 'create_project', lambda payload, **_context: {'resource_id': payload['id']})
    result = project.register_project({'id': 'NewProject'}, actor='alice', access_store=access)
    assert result == {'resource_id': 'NewProject'}
    assert access.updates == []


@pytest.mark.parametrize('status', ['active', 'disabled', 'archived', 'missing'])
def test_user_project_creation_requires_a_registered_active_creator(monkeypatch, status):
    """项目创建只接受已登记且有效的 User

    校验普通 User 的 Project 创建入口、默认授权及创建者管理权。

    :design: decision_4648f7fa3295, decision_b820faf42218
    """
    access = FakeAccessStore()
    if status == 'missing':
        access.users.clear()
    else:
        access.users['alice']['status'] = status
    calls = []
    monkeypatch.setattr(project, 'create_project', lambda *args, **kwargs: calls.append(args) or {'resource_id': 'NewProject'})
    if status == 'active':
        result = project.register_project({'id': 'NewProject'}, actor='alice', access_store=access)
        assert 'initial_admin_ids' not in result
        assert access.updates == []
    else:
        with pytest.raises(PermissionError, match='active Portal user'):
            project.register_project({'id': 'NewProject'}, actor='alice', access_store=access)
        assert calls == access.updates == []


def test_a_project_id_may_use_chinese_letters(monkeypatch, tmp_path):
    """项目标识支持中文且拒绝空格与路径穿越"""
    # sample_admin 10-13: AI员工 keeps its name, and the ID rule takes Chinese letters.
    from management_portal import project_rename

    write_projects("schema_version: 1\nprojects: []\n")
    monkeypatch.setattr(project, "SHARE_ROOT", tmp_path)
    monkeypatch.setattr(project, "PROJECTS_ROOT", tmp_path / "Projects")
    assert project.create_project({"id": "AI员工", "project_type": "company"})["resource_id"] == "AI员工"
    assert (tmp_path / "Projects" / "AI员工" / "summary.md").is_file()
    assert project_rename._clean_new_id("机器人公司") == "机器人公司"
    for bad in ("bad id", "../x", "a/b", "x" * 81):
        with pytest.raises(ValueError):
            project._clean_project_id(bad)
        with pytest.raises(ValueError):
            project_rename._clean_new_id(bad)


def test_a_project_commits_with_its_creator_and_its_place_in_the_permission_trees(monkeypatch, tmp_path):
    """项目登记记录写入创建者，并与它在权限树中的位置同一事务提交

    创建者写进 Project 自己的记录（created_by），权限系统开启时 Project 在登记的事务里放进 R 树并得到“Project 创建人”表项；
    放置失败时登记一起回滚，记录与目录都不留下。
    """
    from management_portal.permission_system import par
    from tests.cases.permission_test_support import enable, user_fact

    write_projects("schema_version: 1\nprojects: []\n")
    monkeypatch.setattr(project, "SHARE_ROOT", tmp_path)
    monkeypatch.setattr(project, "PROJECTS_ROOT", tmp_path / "Projects")
    permissions = enable(monkeypatch)
    permissions.users("alice", category="intern")
    project.create_project({"id": "Made"}, actor="alice")
    assert project_registry.project("Made")["created_by"] == "alice"
    assert permissions.store.paths("R", ["Made"])["Made"][-2:] == ["@projects", "Made"]
    assert permissions.holds("alice", "@admin", "Made")

    def fails(*_args, **_kwargs):
        raise RuntimeError("placing failed")
    monkeypatch.setattr(par.PARPermissions, "sync_resource", fails)
    with pytest.raises(RuntimeError, match="placing failed"):
        project.create_project({"id": "Lost"}, actor="alice")
    assert project_registry.project("Lost") is None
    assert not (tmp_path / "Projects" / "Lost").exists()


def test_a_project_is_named_only_by_its_id():
    """项目仅以标识命名并拒绝额外名称字段"""
    # AI员工 once also showed as AlignMe: its record carried a display name that
    # titled it and found it.  The ID is a Project's only name.
    from management_portal import resource_index_registries

    put_registry_document(project_registry.COLLECTION, project_registry.DOCUMENT_ID, {
        "schema_version": 1, "projects": [{"id": "AI员工", "name": "AlignMe", "status": "active"}]})
    with pytest.raises(KeyError):
        project._project_record("AlignMe")
    record = resource_index_registries.ProjectRegistryAdapter().project_record("project", project_registry.project("AI员工"))
    assert record["title"] == "AI员工" and "name" not in record["attributes"]
    # A record with a display name is not written, and blocks writes until it loses it.
    with pytest.raises(ValueError, match="name"):
        with project_registry.mutate() as document:
            document["projects"][0]["status"] = "paused"
    with pytest.raises(ValueError, match="name"):
        write_projects("schema_version: 1\nprojects:\n  - id: RobotWorker\n    name: Robots\n")
    with project_registry.mutate() as document:
        document["projects"][0].pop("name")
    assert project_registry.project("AI员工") == {"id": "AI员工", "status": "active"}


def test_a_project_record_holds_only_the_fields_portal_reads():
    """项目注册记录仅保留 Portal 使用的字段"""
    # FlowTango and DRAM Stacking carried a legacy db_name that nothing read: a
    # copy nobody keeps current, like the display name.
    put_registry_document(project_registry.COLLECTION, project_registry.DOCUMENT_ID, {
        "schema_version": 1, "projects": [{"id": "FlowTango", "status": "active", "db_name": "flowtango_db"}]})
    with pytest.raises(ValueError, match="FlowTango.db_name"):
        with project_registry.mutate() as document:
            document["projects"][0]["status"] = "paused"
    with project_registry.mutate() as document:
        document["projects"][0].pop("db_name")
        document["projects"].append({"id": "Full", **{field: "" for field in project_registry.PROJECT_FIELDS - {"id"}}})
    assert project_registry.project("FlowTango") == {"id": "FlowTango", "status": "active"}

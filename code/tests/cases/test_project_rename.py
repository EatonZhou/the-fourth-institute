"""RenameProject: a Project's ID and directory change together (decision_6591e7793116).

:importance: P1
:design: decision_6591e7793116
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from management_common.portal_database import _database_schema, portal_database
from management_portal import project_registry, project_rename
from management_portal.hatg import catalog
from management_portal.project_rename import ProjectRename, RenameBlocked, Rewriter
from management_portal.resources import isa as resource_isa
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.registry_support import write_projects

SHARE = Path("/share")
CHANGE = ProjectRename("ACLink", "ACLinkNext", SHARE / "Projects/ACLink", SHARE / "Projects/ACLinkNext", share_root=SHARE)


@pytest.mark.parametrize(("before", "after"), [
    ("/share/Projects/ACLink", "/share/Projects/ACLinkNext"),
    ("cd /share/Projects/ACLink/src && make", "cd /share/Projects/ACLinkNext/src && make"),
    ("见 `/share/Projects/ACLink`。", "见 `/share/Projects/ACLinkNext`。"),
    ("目录 /share/Projects/ACLink.", "目录 /share/Projects/ACLinkNext."),
    ("/Projects/ACLink", "/Projects/ACLinkNext"),
    ("file:///share/Projects/ACLink/a.pdf", "file:///share/Projects/ACLinkNext/a.pdf"),
    # Other directories that start the same way, or contain the path.
    ("/share/Projects/ACLink-Co-Kernel/x", "/share/Projects/ACLink-Co-Kernel/x"),
    ("/share/Projects/ACLinkCoKernel", "/share/Projects/ACLinkCoKernel"),
    ("/share/Projects/ACLink.md", "/share/Projects/ACLink.md"),
    ("/data/share/Projects/ACLink/x", "/data/share/Projects/ACLink/x"),
    ("/home/u/Projects/ACLink", "/home/u/Projects/ACLink"),
])
def test_paths_change_only_under_the_old_directory(before, after):
    """Project 改名只改写旧目录下的路径

    旧目录的绝对路径与共享根相对路径改到新目录；名字以旧目录开头的其他目录、包含该路径的更长路径保持不变。
    """
    assert Rewriter(CHANGE).value(before) == after


def test_the_old_id_changes_where_it_is_a_whole_value_key_or_project_segment():
    """Project 改名按整值、JSON 键与 project 键段改写旧 ID

    值等于旧 ID 的字段、成员角色等 JSON 键与 project:<ID> 键段改为新 ID；包含旧 ID 的其他文本和以它开头的其他 ID 保持不变。
    """
    rewriter = Rewriter(CHANGE)
    document = {
        "project_id": "ACLink", "project_ids": ["ACLink", "ACLinkCoKernel"], "title": "ACLink 周报",
        "memberships": {"ACLink": "admin", "Other": "viewer"},
        "binding_id": "portal-chat:sample_admin:project:project:ACLink",
        "workspace_path": "/share/Projects/ACLink/agents/x", "count": 3,
    }
    assert rewriter.document(document) == {
        "project_id": "ACLinkNext", "project_ids": ["ACLinkNext", "ACLinkCoKernel"], "title": "ACLink 周报",
        "memberships": {"ACLinkNext": "admin", "Other": "viewer"},
        "binding_id": "portal-chat:sample_admin:project:project:ACLinkNext",
        "workspace_path": "/share/Projects/ACLinkNext/agents/x", "count": 3,
    }
    assert rewriter.value("ACLink") == "ACLinkNext"
    assert rewriter.value("project:ACLinkCoKernel") == "project:ACLinkCoKernel"


@pytest.mark.parametrize("options", [
    {"ensure_ascii": False, "separators": (",", ":"), "sort_keys": True},
    {"ensure_ascii": True, "separators": (",", ":"), "sort_keys": False},
    {"ensure_ascii": False, "separators": (", ", ": "), "sort_keys": False},
    {"ensure_ascii": False, "indent": 2},
])
def test_a_json_column_keeps_its_encoding(options):
    """Project 改名保持 JSON 列原有编码

    JSON 列按原来的键顺序、分隔符与 ensure_ascii 写回；不涉及改名的值逐字节不变，非 ASCII 旧 ID 也能被查到。

    :importance: P2
    """
    change = ProjectRename("AI员工", "AIStaff", SHARE / "Projects/AI员工", SHARE / "Projects/AIStaff")
    value = {"z": "AI员工", "a": {"path": "/share/Projects/AI员工/kb", "note": "员工"}}
    text = json.dumps(value, **options)
    expected = {"z": "AIStaff", "a": {"path": "/share/Projects/AIStaff/kb", "note": "员工"}}
    assert Rewriter(change).column(text) == json.dumps(expected, **options)
    # Values that name neither stay byte for byte, whatever their encoding.
    untouched = json.dumps({"b": 1, "a": "x"}, **options)
    assert Rewriter(change).column(untouched) == untouched
    assert any("\\u5458" in needle for needle in Rewriter(change).needles())


def test_project_files_get_the_new_title_marks_and_paths():
    """Project 改名更新项目文件标题与 #project 标记

    summary.md 与 agent_port/boot.md 标题中的项目 ID 和 #project("<ID>") 标记改为新 ID，其他标题与其他项目的标记不变。

    :importance: P2
    """
    rewriter = Rewriter(CHANGE)
    assert rewriter.file_text("# ACLink\n\nACLink 的目标\n", "summary.md") == "# ACLinkNext\n\nACLink 的目标\n"
    assert rewriter.file_text("# ACLink 项目指南\n", "agent_port/boot.md") == "# ACLinkNext 项目指南\n"
    assert rewriter.file_text("# ACLink\n", "notes.md") == "# ACLink\n"
    assert rewriter.file_text('见 #project("ACLink") 与 #project(\'ACLinkCoKernel\')', "a.md") == (
        '见 #project("ACLinkNext") 与 #project(\'ACLinkCoKernel\')'
    )


def test_rename_is_a_declared_resource_instruction():
    """RenameProject 由 ResourceService 执行并登记处理函数

    RenameProject 由 ResourceService 执行、会改变状态，并登记了处理函数。

    :design: decision_b820faf42218
    """
    declaration = catalog.instruction_catalog()["RenameProject"]
    assert declaration.owner == "resource" and declaration.mutates
    assert resource_isa.HANDLERS["RenameProject"] is resource_isa.rename_project


# -- the whole rename against the test database --------------------------------------


class _Residents:
    def __init__(self, busy=()):
        self.busy_ids = list(busy)
        self.calls = []

    def close_intake(self, ids):
        self.calls.append(("close", list(ids)))
        return list(ids)

    def busy(self, ids):
        return [item for item in ids if item in self.busy_ids]

    def open_intake(self, ids):
        self.calls.append(("open", list(ids)))


def _git(*arguments, cwd):
    subprocess.run(["git", *arguments], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


@pytest.fixture
def world(monkeypatch, tmp_path, permissions):
    projects = tmp_path / "Projects"
    old_root = projects / "Old"
    (old_root / "agent_port").mkdir(parents=True)
    (old_root / "summary.md").write_text("# Old\n\n目标\n", encoding="utf-8")
    (old_root / "agent_port/boot.md").write_text("# Old 项目指南\n", encoding="utf-8")
    (old_root / "notes.md").write_text(
        f"代码在 {old_root}/src，另见 {projects}/OldX 与 #project(\"Old\")。\n", encoding="utf-8")
    (projects / "OldX").mkdir()
    # A worktree in the Project of a repository outside it.
    repository = tmp_path / "repository"
    repository.mkdir()
    _git("init", "-q", cwd=repository)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=repository)
    _git("worktree", "add", "-q", str(old_root / "wt"), cwd=repository)
    agents = tmp_path / "Agents"
    agents.mkdir()
    (agents / "notes").symlink_to(old_root / "notes.md")
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/config.toml").write_text(f'[projects."{old_root}/src"]\ntrust_level = "trusted"\n')
    claude = home / ".claude-accounts/one/projects"
    (claude / project_rename._claude_project_key(str(old_root)) / "memory").mkdir(parents=True)
    kimi = tmp_path / "kimi"
    kimi.mkdir()
    (kimi / "workspaces.json").write_text(json.dumps({"workspaces": {"w": {"root": str(old_root)}}}))
    services = tmp_path / "Services"
    services.mkdir()
    (services / "job.py").write_text(f'ROOT = "{old_root}"\n')
    # A paper in the shared library, outside the Project, related to it and to another Project.
    from management_portal import paper_store

    paper = tmp_path / "Papers/items/2026/paper-one/manifest.json"
    paper.parent.mkdir(parents=True)
    paper.write_text(json.dumps({"paper_id": "paper_aaaaaaaaaaaa", "relations": {"projects": [
        {"project_id": "Old", "relation": "relevant"}, {"project_id": "OldX", "relation": "relevant"}]}}))
    monkeypatch.setattr(paper_store, "PAPER_STORE", paper_store.PaperStore(tmp_path / "Papers"))
    units = tmp_path / "units"
    units.mkdir()
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("")
    for name, value in {
        "PROJECTS_ROOT": projects, "SHARE_ROOT": tmp_path, "HOME": home, "KIMI_HOME": kimi,
        "SYMLINK_ROOTS": (projects, agents), "SERVICE_SCRIPT_ROOTS": (services,),
        "SYSTEMD_UNIT_DIRS": (units,), "MOUNTINFO": mountinfo,
    }.items():
        monkeypatch.setattr(project_rename, name, value)

    write_projects({"schema_version": 1, "status_vocab": ["active"], "tag_vocab": [], "projects": [
        {"id": "Old", "path": str(old_root), "status": "active", "description": "d"},
        {"id": "Taken", "path": str(projects / "Taken"), "status": "active"},
    ]})
    project_registry.reset_cache_for_tests()

    # Records in a Portal schema of this test database, and a history table.
    probe = _database_schema("rename_probe")
    database = portal_database(schema="rename_probe")
    with database.transaction() as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{probe}" CASCADE')
        connection.execute(f'CREATE SCHEMA "{probe}"')
        connection.execute(f'CREATE TABLE "{probe}".items (id TEXT PRIMARY KEY, title TEXT, doc TEXT, data JSONB)')
        connection.execute(f'CREATE TABLE "{probe}".instruction_executions (id TEXT, payload TEXT)')
        connection.execute(f'CREATE TABLE "{probe}".project_links (actor TEXT PRIMARY KEY, project_id TEXT NOT NULL)')
        connection.execute(f'INSERT INTO "{probe}".items VALUES (%s,%s,%s,%s::jsonb)', (
            "Old", "Old", json.dumps({"memberships": {"Old": "admin"}, "path": f"{old_root}/agents/r1",
                                      "other": f"{projects}/OldX"}, ensure_ascii=True),
            json.dumps({"project_ids": ["Old"], "binding": "portal-chat:u:project:project:Old"}),
        ))
        connection.execute(f'INSERT INTO "{probe}".items VALUES (%s,%s,%s,%s::jsonb)', (
            "OldX", "OldX", json.dumps({"path": f"{projects}/OldX"}), json.dumps({"project_ids": ["OldX"]}),
        ))
        connection.execute(f'INSERT INTO "{probe}".instruction_executions VALUES (%s,%s)', ("i1", f"Old {old_root}"))
        connection.execute(f'INSERT INTO "{probe}".project_links VALUES (%s,%s)', ("alice", "Old"))

    # The Projects and an article in Old in the permission trees, and manual entries for alice on them.
    permissions.users("alice", category="intern")
    permissions.place(Fact("project", "Old"), Fact("project", "Taken"),
                      Fact("article", "article_aaaaaaaaaaaa", {"project": "Old"}))
    permissions.grant("alice", "Old", "@admin")
    permissions.grant("alice", "Taken", "@viewer")
    yield {"projects": projects, "old_root": old_root, "new_root": projects / "New", "repository": repository,
           "paper": paper,
           "agents": agents, "home": home, "kimi": kimi, "units": units, "probe": probe,
           "permissions": permissions}
    with database.transaction() as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{probe}" CASCADE')
    project_registry.reset_cache_for_tests()


def _permission_nodes(world):
    """``{id: path}`` of the R elements the fixture placed, under either Project ID."""
    return world["permissions"].store.paths("R", ["Old", "New", "Taken", "article_aaaaaaaaaaaa"])


def _probe_rows(world, table):
    with portal_database(schema="rename_probe").snapshot() as connection:
        return [tuple(row) for row in connection.execute(
            f'SELECT * FROM "{world["probe"]}".{table} ORDER BY 1').fetchall()]


def test_a_rename_moves_the_directory_and_every_reference_follows(world, monkeypatch):
    """Project 改名移动目录且所有引用跟随

    改名后目录移到新 ID，登记、数据库记录、成员角色、权限树中的 Project 元素与其下资源的路径、权限表项、项目文件、git worktree 链接、符号链接与执行环境状态都指向新 ID 和新目录；历史记录不改写，状态版本与登记版本前进。
    """
    residents = _Residents()
    old_root, new_root, projects = world["old_root"], world["new_root"], world["projects"]
    history_tables = ("executions", "execution_token_usage_events", "execution_account_usage_hourly")
    # Exercise the runtime domain's history rules using this fixture's isolated tables.
    schemas = project_rename._portal_schemas
    monkeypatch.setattr(project_rename, "_portal_schemas", lambda connection: {
        schema: "execution_runtime" if schema == world["probe"] else name
        for schema, name in schemas(connection).items()
    })
    with portal_database(schema="rename_probe").transaction() as connection:
        for table in history_tables:
            connection.execute(f'CREATE TABLE "{world["probe"]}".{table} (id TEXT, payload TEXT)')
            connection.execute(f'INSERT INTO "{world["probe"]}".{table} VALUES (%s,%s)',
                               ("e1", f"Old {old_root}"))
    result = project_rename.rename_project("Old", "New", "alice", residents=residents)

    assert (result["project_id"], result["previous_project_id"]) == ("New", "Old")
    assert not old_root.exists() and (new_root / "summary.md").is_file()
    # The registry: the new ID at the new path, the other Project untouched.
    assert [(item["id"], item["path"]) for item in project_registry.projects()] == [
        ("New", str(new_root)), ("Taken", str(projects / "Taken"))]
    # Database records; history keeps what it said.
    items = {row[0]: row for row in _probe_rows(world, "items")}
    assert set(items) == {"New", "OldX"}
    assert items["New"][1] == "New"
    doc = json.loads(items["New"][2])
    assert doc == {"memberships": {"New": "admin"}, "path": f"{new_root}/agents/r1", "other": f"{projects}/OldX"}
    assert "\\u" not in items["New"][2] or items["New"][2].isascii()
    assert items["New"][3] == {"project_ids": ["New"], "binding": "portal-chat:u:project:project:New"}
    assert items["OldX"][3] == {"project_ids": ["OldX"]}
    assert _probe_rows(world, "instruction_executions") == [("i1", f"Old {old_root}")]
    assert _probe_rows(world, "project_links") == [("alice", "New")]
    assert result["database"]["tables"]["execution_runtime.items"] == 1
    for table in history_tables:
        assert _probe_rows(world, table) == [("e1", f"Old {old_root}")]
        assert f"execution_runtime.{table}" not in result["database"]["tables"]
    # Permissions: the Project's element and the paths under it carry the new ID; entries follow.
    nodes = _permission_nodes(world)
    assert set(nodes) == {"New", "Taken", "article_aaaaaaaaaaaa"}
    assert nodes["New"][-1] == "New" and nodes["article_aaaaaaaaaaaa"][-2:] == ["New", "article_aaaaaaaaaaaa"]
    # The Project's P and R elements and the article under it.
    assert result["database"]["tables"]["permission.scheme_nodes.path"] == 3
    permissions = world["permissions"]
    assert permissions.holds("alice", "@admin", "New")
    assert permissions.holds("alice", "@admin", "article_aaaaaaaaaaaa")
    assert permissions.holds("alice", "@viewer", "Taken") and not permissions.holds("alice", "@contributor", "Taken")
    # Files in the Project, the worktree link outside it and symlinks into it.
    assert (new_root / "summary.md").read_text(encoding="utf-8") == "# New\n\n目标\n"
    assert (new_root / "agent_port/boot.md").read_text(encoding="utf-8") == "# New 项目指南\n"
    assert (new_root / "notes.md").read_text(encoding="utf-8") == (
        f"代码在 {new_root}/src，另见 {projects}/OldX 与 #project(\"New\")。\n")
    assert subprocess.run(["git", "status", "--short"], cwd=new_root / "wt", capture_output=True).returncode == 0
    assert subprocess.run(["git", "worktree", "list"], cwd=world["repository"], capture_output=True,
                          text=True).stdout.count(str(new_root / "wt")) == 1
    assert os.readlink(world["agents"] / "notes") == str(new_root / "notes.md")
    # Provider state kept per working directory.
    assert f'[projects."{new_root}/src"]' in (world["home"] / ".codex/config.toml").read_text()
    claude = world["home"] / ".claude-accounts/one/projects"
    assert (claude / project_rename._claude_project_key(str(new_root)) / "memory").is_dir()
    assert json.loads((world["kimi"] / "workspaces.json").read_text())["workspaces"]["w"]["root"] == str(new_root)
    assert result["files"].get("errors") is None
    # Papers outside the Project follow the new ID (DRAM Stacking's kept the old one).
    relations = json.loads(world["paper"].read_text())["relations"]["projects"]
    assert [item["project_id"] for item in relations] == ["New", "OldX"]
    # Every feature with Project relations reports; this Project has no research map directions.
    assert result["relations"] == {"paper": ["paper_aaaaaaaaaaaa"], "projects": []}
    assert result["external_references"] == [str(project_rename.SERVICE_SCRIPT_ROOTS[0] / "job.py")]
    assert residents.calls[0][0] == "close" and residents.calls[-1][0] == "open"


def test_a_dry_run_reports_and_changes_nothing(world):
    """Project 改名预演只报告不修改

    dry_run 返回将改写的数据库行、权限树路径与文件，目录、登记、数据库与权限树保持不变。

    :importance: P2
    """
    result = project_rename.rename_project("Old", "New", "alice", dry_run=True, residents=_Residents())
    assert result["dry_run"] and result["blockers"] == [] and result["busy_resident_ids"] == []
    assert result["database"]["tables"]["rename_probe.items"] == 1
    assert result["database"]["tables"]["permission.scheme_nodes.path"] == 3
    assert set(_permission_nodes(world)) == {"Old", "Taken", "article_aaaaaaaaaaaa"}
    assert str(world["old_root"] / "notes.md") in result["files"]["rewritten"]
    # Every feature with Project relations reports; this Project has no research map directions.
    assert result["relations"] == {"paper": ["paper_aaaaaaaaaaaa"], "projects": []}
    assert '"Old"' in world["paper"].read_text()
    assert world["old_root"].is_dir() and not world["new_root"].exists()
    assert [item["id"] for item in project_registry.projects()] == ["Old", "Taken"]
    assert {row[0] for row in _probe_rows(world, "items")} == {"Old", "OldX"}


def test_a_running_resident_blocks_the_rename_and_intake_reopens(world, monkeypatch):
    """Resident 正在执行时 Project 改名被拒绝且恢复接单

    项目中的 Resident 正在执行时拒绝改名，之前暂停的接单恢复，目录不移动。
    """
    monkeypatch.setattr(project_rename, "affected_residents", lambda *_args: ["resident_aaaaaaaaaaaa"])
    residents = _Residents(busy=["resident_aaaaaaaaaaaa"])
    with pytest.raises(RenameBlocked) as error:
        project_rename.rename_project("Old", "New", "alice", residents=residents)
    assert error.value.details == {"busy_resident_ids": ["resident_aaaaaaaaaaaa"]}
    assert residents.calls == [("close", ["resident_aaaaaaaaaaaa"]), ("open", ["resident_aaaaaaaaaaaa"])]
    assert world["old_root"].is_dir() and not world["new_root"].exists()


def test_a_failed_database_rewrite_moves_the_directory_back(world, monkeypatch):
    """数据库改写失败时 Project 目录移回原处

    目录移动后数据库事务失败时目录移回原处、接单恢复、登记不变。
    """
    residents = _Residents()

    def fail(*_args, **kwargs):
        if kwargs.get("apply"):
            assert world["new_root"].is_dir()
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(project_rename, "rewrite_database", fail)
    with pytest.raises(RuntimeError):
        project_rename.rename_project("Old", "New", "alice", residents=residents)
    assert world["old_root"].is_dir() and not world["new_root"].exists()
    assert residents.calls[-1][0] == "open"
    assert [item["id"] for item in project_registry.projects()] == ["Old", "Taken"]


def test_mounts_and_services_outside_portal_block_the_rename(world, monkeypatch):
    """Portal 之外的挂载和服务阻止 Project 改名

    项目目录中有挂载点或 systemd 服务配置使用该目录时拒绝改名，预演列出阻止项；只引用其他目录的服务不算。
    """
    unit = world["units"] / "lab.service"
    unit.write_text(f"[Service]\nWorkingDirectory={world['old_root']}/src/backend\n")
    (world["units"] / "other.service").write_text(f"[Service]\nWorkingDirectory={world['projects']}/OldX\n")
    mountinfo = project_rename.MOUNTINFO
    mountinfo.write_text(f"36 35 8:1 /x {str(world['old_root']).replace(' ', chr(92) + '040')}/data rw - ext4 /dev/sda rw\n")
    preview = project_rename.rename_project("Old", "New", "alice", dry_run=True, residents=_Residents())
    assert preview["blockers"] == [{"kind": "mount", "path": f"{world['old_root']}/data"},
                                   {"kind": "systemd_unit", "path": str(unit)}]
    residents = _Residents()
    with pytest.raises(RenameBlocked):
        project_rename.rename_project("Old", "New", "alice", residents=residents)
    assert residents.calls == [] and world["old_root"].is_dir()


@pytest.mark.parametrize(("new_id", "error"), [
    ("Taken", RenameBlocked), ("bad id", ValueError), ("../x", ValueError), ("Old", ValueError),
])
def test_invalid_targets_change_nothing(world, new_id, error):
    """无效的新 Project ID 不改变任何内容

    新 ID 已被使用、不符合 ID 规则或与现在相同时拒绝，目录不动。

    :importance: P2
    """
    with pytest.raises(error):
        project_rename.rename_project("Old", new_id, "alice", residents=_Residents())
    assert world["old_root"].is_dir()


def test_an_existing_target_directory_blocks_the_rename(world):
    """目标目录已存在时拒绝 Project 改名

    /share/Projects/<新 ID> 已存在时拒绝改名，不覆盖已有内容。

    :importance: P2
    """
    world["new_root"].mkdir()
    with pytest.raises(RenameBlocked):
        project_rename.rename_project("Old", "New", "alice", residents=_Residents())


def test_a_project_id_that_is_also_a_status_cannot_be_renamed_by_id(world):
    """与 Project 状态同名的 ID 不能按 ID 改名

    旧 ID 同时是 Project 状态或知识标签时拒绝，避免把同名的状态值一起改掉。

    :importance: P2
    """
    write_projects({"schema_version": 1, "status_vocab": ["Old"], "tag_vocab": [], "projects": [
        {"id": "Old", "path": str(world["old_root"]), "status": "Old"}]})
    project_registry.reset_cache_for_tests()
    with pytest.raises(RenameBlocked):
        project_rename.rename_project("Old", "New", "alice", residents=_Residents())

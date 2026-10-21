"""Rename follows current Workspace references while preserving deletion facts."""
import pytest

from management_portal import project_rename
from management_portal.society_service import resource_client
from management_portal.society_service.state_store import LineageDataStore
from management_portal.resources.workspace_store import WorkspaceStore
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_project_rename import _Residents, world


ACTIVE = "resident_111111111111"
DELETED = "resident_222222222222"
ORPHAN = "resident_333333333333"
WORKSPACE = "workspace_111111111111"
OWNED = "workspace_444444444444"
LINEAGE = "lineage_111111111111"


@pytest.fixture
def identities(world, monkeypatch):
    store = LineageDataStore()
    workspaces = WorkspaceStore()
    workspaces.ensure(WORKSPACE, str(world["old_root"] / "src"))
    workspaces.ensure(OWNED, str(world["old_root"] / "resident/workspace"))
    for suffix in ("222222222222", "333333333333"):
        wid = "workspace_" + suffix
        workspaces.ensure(wid, str(world["old_root"] / suffix))
        with workspaces.database.transaction() as connection:
            workspaces.mark_deleted(wid, deleted_at="2026-10-08T00:00:00Z",
                                    deleted_by="sample_admin", connection=connection)
    with store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO lineages (lineage_id,title,project_id,leader_resident_id,revision,layout_revision,created_by) VALUES (%s,%s,%s,%s,1,1,%s)",
            (LINEAGE, "Old", "Old", ACTIVE, "sample_admin"),
        )
        for ordinal, (rid, wid, deleted) in enumerate((
            (ACTIVE, WORKSPACE, False),
            (DELETED, "workspace_222222222222", True),
            (ORPHAN, "workspace_333333333333", True),
        )):
            connection.execute(
                "INSERT INTO residents(resident_id,title,description,deleted_at,deleted_by,historical_project_id,owned_workspace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (rid, "Old", f"Work in {world['old_root']}/src",
                 "2026-10-08T00:00:00Z" if deleted else None,
                 "sample_admin" if deleted else None, "Old" if deleted else None, None if deleted else OWNED),
            )
            connection.execute(
                "INSERT INTO resident_workspace_bindings VALUES (%s,%s,%s)",
                (rid, wid, "task files"),
            )
            if rid != ORPHAN:
                # A stale graph row must not make a deleted identity runnable.
                connection.execute("INSERT INTO lineage_nodes(node_id,lineage_id,node_type) VALUES (%s,%s,'resident')",
                                   (rid, LINEAGE))
    original_paths = resource_client.workspace_paths_many
    calls = []

    def current_paths(ids, *, connection=None):
        ids = list(ids)
        assert set(ids) <= {WORKSPACE, OWNED}, "Deleted Workspaces must not be required"
        calls.append(ids)
        return original_paths(ids, connection=connection)

    monkeypatch.setattr(resource_client, "workspace_paths_many", current_paths)
    return world, store, calls


def test_rename_affected_identities_exclude_deleted_graph_and_bindings(identities):
    """项目重命名仅选取存活身份及当前 Workspace 绑定

    :design: decision_6591e7793116
    :design-point:
    """
    world, store, calls = identities
    change = project_rename.plan("Old", "New")
    assert project_rename.affected_residents(change, project_rename.Rewriter(change)) == [ACTIVE]
    assert calls == [[WORKSPACE, OWNED]]
    assert not (world["old_root"] / "src").exists()
    with store.database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM resident_workspace_bindings").fetchone()[0] == 3


def test_rename_provider_paths_recheck_deleted_identity_at_read_time(identities):
    """重命名读取路径时重新排除已删除的身份

    :design: decision_6591e7793116
    :design-point:
    """
    world, store, calls = identities
    change = project_rename.plan("Old", "New")
    workspaces = WorkspaceStore()
    workspaces.ensure(WORKSPACE, str(world["new_root"] / "src"), move=True)
    workspaces.ensure(OWNED, str(world["new_root"] / "resident/workspace"), move=True)
    assert project_rename._resident_paths(change, [ACTIVE, DELETED, ORPHAN]) == [
        (str(world["old_root"] / "resident/workspace"), str(world["new_root"] / "resident/workspace")),
        (str(world["old_root"] / "src"), str(world["new_root"] / "src")),
    ]
    store.database.write(lambda connection: connection.execute(
        "UPDATE residents SET deleted_at=%s WHERE resident_id=%s", ("2026-10-08T01:00:00Z", ACTIVE),
    ))
    assert project_rename._resident_paths(change, [ACTIVE, DELETED, ORPHAN]) == []
    assert calls == [[WORKSPACE, OWNED], []]


def test_rename_preserves_deletion_project_facts_and_never_hydrates_history(identities):
    """项目重命名保留已删除身份的历史项目和路径

    :design: decision_6591e7793116
    :design-point:
    """
    world, store, calls = identities
    change = project_rename.plan("Old", "New")

    def historical_rows():
        with store.database.snapshot() as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT * FROM residents WHERE deleted_at IS NOT NULL ORDER BY resident_id",
            ).fetchall()]

    before = historical_rows()
    workspaces = WorkspaceStore()
    original_workspaces = workspaces.rows([WORKSPACE, OWNED, "workspace_222222222222"], include_deleted=True)
    plan = project_rename.rewrite_database(change, project_rename.Rewriter(change), apply=False)
    assert plan.tables["lineage.residents"] == 1
    assert historical_rows() == before
    control = _Residents()
    result = project_rename.rename_project("Old", "New", "alice", residents=control)
    assert result["resident_ids"] == [ACTIVE]
    assert control.calls == [("close", [ACTIVE]), ("open", [ACTIVE])]
    assert historical_rows() == before
    assert not (world["new_root"] / "src").exists()
    with store.database.snapshot() as connection:
        assert tuple(connection.execute(
            "SELECT title,description FROM residents WHERE resident_id=%s", (ACTIVE,),
        ).fetchone()) == ("New", f"Work in {world['new_root']}/src")
        for rid in (DELETED, ORPHAN):
            assert tuple(connection.execute(
                "SELECT title,description,historical_project_id FROM residents WHERE resident_id=%s", (rid,),
            ).fetchone()) == ("Old", f"Work in {world['old_root']}/src", "Old")
        assert connection.execute("SELECT project_id FROM lineages WHERE lineage_id=%s", (LINEAGE,)).fetchone()[0] == "New"
        assert connection.execute("SELECT count(*) FROM resident_workspace_bindings").fetchone()[0] == 3
    assert calls == [[WORKSPACE, OWNED], [WORKSPACE, OWNED]]
    after = workspaces.rows(original_workspaces, include_deleted=True)
    assert after["workspace_222222222222"] == original_workspaces["workspace_222222222222"]
    for wid in (WORKSPACE, OWNED):
        assert after[wid]["updated_at"] > original_workspaces[wid]["updated_at"]
        assert after[wid]["path"].startswith(str(world["new_root"]) + "/")


def test_rename_finds_owned_workspace_even_without_membership_or_extra_binding(identities):
    """项目重命名可通过自有 Workspace 找到无成员关系的 Resident"""
    world, store, calls = identities
    with store.database.transaction() as connection:
        connection.execute("DELETE FROM lineage_nodes WHERE node_id=%s", (ACTIVE,))
        connection.execute("DELETE FROM resident_workspace_bindings WHERE resident_id=%s", (ACTIVE,))
    change = project_rename.plan("Old", "New")
    assert project_rename.affected_residents(change, project_rename.Rewriter(change)) == [ACTIVE]
    assert calls == [[OWNED]]

"""Relational graph cutover preserves identity, executable settings and references.

:module: spec.design-governance
"""
import copy

import pytest

from management_portal.society_service import definition_collections, graph_storage
from management_portal.society_service.lineage_data_runtime import RuntimeCASConflict
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.isa_test_support import ok
from tests.cases.test_family_familize import charters, lineage, resident, future_creation_store  # noqa: F401


class RollbackTrial(Exception):
    pass


def test_relational_graph_preserves_family_and_changes_only_edited_rows(lineage, charters):
    """关系型图存储保留 Family 门面节点，只重写改动过的行"""
    member = resident(lineage, "Member")
    family = ok("Familize", {"lineage_id": lineage, "resident_ids": [member], "title": "Relational Family"})
    state = get_lineage_data_store()
    with pytest.raises(RollbackTrial), state.database.transaction() as c:
        before = graph_storage.read_many(c, [lineage])[lineage]
        before.update(definition_collections.read_many(c, [lineage])[lineage])
        assert family["facade_resident_id"] in {n["id"] for n in before["nodes"]}
        assert c.execute("SELECT count(*) n FROM lineage_nodes WHERE node_id=%s", (member,)).fetchone()["n"] == 1
        component = before["program"]["components"][0]
        untouched = c.execute("SELECT ctid::text token FROM lineage_components WHERE node_id=%s", (component["id"],)).fetchone()["token"]
        value = copy.deepcopy(before)
        value["fragments"] = [{"id": "fragment_0123456789ab", "content": "A fragment", "attachments": []}]
        graph_storage.put(c, lineage, value)
        definition_collections.put(c, lineage, value)
        assert c.execute("SELECT ctid::text token FROM lineage_components WHERE node_id=%s", (component["id"],)).fetchone()["token"] == untouched
        assert definition_collections.read_many(c, [lineage])[lineage]["fragments"][0]["content"] == "A fragment"
        raise RollbackTrial()


def test_unknown_fragment_fields_are_rejected(lineage):
    """Lineage 片段中的未知字段被拒绝"""
    state = get_lineage_data_store()
    with pytest.raises(ValueError, match="Unknown fragments fields"), state.database.transaction() as c:
        definition_collections.put(c, lineage, {"fragments": [{"id": "fragment_0123456789ab", "content": "x", "unbounded": {"anything": True}}]})


def test_missing_resident_reference_rolls_back_entire_graph(lineage):
    """图中引用不存在的 Resident 时整张图写入回滚"""
    from psycopg.errors import ForeignKeyViolation
    state = get_lineage_data_store()
    before = state.lineage_definition(lineage)
    candidate = copy.deepcopy(before)
    candidate['nodes'].append({'id': 'resident_ffffffffffff'})
    with pytest.raises(ForeignKeyViolation):
        state.put_lineage_definition(lineage, candidate, {})
    assert state.lineage_definition(lineage) == before


def test_deleted_lineage_rejects_late_definition_write(lineage):
    """Lineage 删除后迟到的定义写入被拒绝，删除时间和删除人保留"""
    state = get_lineage_data_store()
    before = state.lineage_definition(lineage)
    state.remove_lineage(lineage, deleted_by='sample_admin')
    with pytest.raises(RuntimeCASConflict, match='Lineage has been deleted'):
        state.put_lineage_definition(lineage, before, {})
    with state.database.read() as c:
        row = c.execute('SELECT deleted_at,deleted_by FROM lineages WHERE lineage_id=%s', (lineage,)).fetchone()
        assert row['deleted_at'] and row['deleted_by'] == 'sample_admin'




def test_deleting_workspace_owner_retires_nested_bindings_and_keeps_external_workspace(lineage, tmp_path):
    """删除所有者只退役无存活引用的工作区，共享嵌套及外部绑定保持不变"""
    from management_portal.resources.workspace_store import get_workspace_store
    state = get_lineage_data_store()
    owner, member = resident(lineage, 'Workspace owner'), resident(lineage, 'Workspace reader')
    record = state.resident_record(owner)
    ws = get_workspace_store()
    with state.database.read() as c:
        original = {r['workspace_id'] for r in c.execute('SELECT workspace_id FROM resident_workspace_bindings WHERE resident_id=%s', (member,)).fetchall()}
        operations_before = c.execute('SELECT * FROM resident_lifecycle_operations WHERE resident_id=%s ORDER BY operation_id', (member,)).fetchall()
    nested, external, unused = 'workspace_0123456789ab', 'workspace_0123456789ac', 'workspace_0123456789ad'
    ws.ensure(nested, record['owned_workspace']['path'] + '/family_memory')
    ws.ensure(unused, record['owned_workspace']['path'] + '/unused')
    ws.ensure(external, str(tmp_path / 'shared'))
    with state.database.transaction() as c:
        c.execute('INSERT INTO resident_workspace_bindings(resident_id,workspace_id,purpose) VALUES(%s,%s,%s),(%s,%s,%s)',
                  (member, nested, 'Family memory', member, external, 'Shared work'))
    before = ws.rows([nested, external])
    tombstone = {'resident_id': owner, 'deleted_at': '2026-10-10T00:00:00Z', 'deleted_by': 'sample_admin'}
    retired = state.remove_residents([tombstone])
    assert ws.rows([nested, external]) == before
    assert ws.rows([unused, record['owned_workspace_id']]) == {}
    assert set(retired) == {record['owned_workspace']['path'], record['owned_workspace']['path'] + '/unused'}
    history = ws.rows([unused, record['owned_workspace_id']], include_deleted=True)
    assert all(row['deleted_at'] and row['deleted_by'] == 'sample_admin' for row in history.values())
    assert state.remove_residents([tombstone]) == []
    with state.database.read() as c:
        assert {r['workspace_id'] for r in c.execute('SELECT workspace_id FROM resident_workspace_bindings WHERE resident_id=%s', (member,)).fetchall()} == original | {nested, external}
        assert c.execute('SELECT * FROM resident_lifecycle_operations WHERE resident_id=%s ORDER BY operation_id', (member,)).fetchall() == operations_before

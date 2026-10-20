import pytest

from management_portal.society_service.state_store import LineageDataStore, lineage_data_schema_matches
from tests.cases.test_lineage_catalogs import LINEAGE_ID, _lineage_definition, _record

FIRST = "resident_111111111111"
SECOND = "resident_222222222222"


def test_activity_identity_read_preserves_order_without_workspace_reads(monkeypatch):
    """批量读取 Resident 身份记录时按请求顺序去重，且不读取 Workspace 路径

    :design: decision_71aa039e3211
    :design-point:
    """
    from management_portal.society_service import resource_client

    store = LineageDataStore()
    store.put_lineage_definition(LINEAGE_ID, _lineage_definition(), {})
    for rid in (FIRST, SECOND):
        store.put_resident_record(rid, _record(rid))
    expected = {item['resident_id']: item for item in store.residents_by_id([FIRST, SECOND])}

    def unrelated_view(*args, **kwargs):
        raise AssertionError('activity identity read assembled an unrelated view')

    monkeypatch.setattr(resource_client, 'workspace_paths_many', unrelated_view)
    records = store.resident_identity_records([SECOND, FIRST, SECOND, 'resident_ffffffffffff'])
    assert [item['resident_id'] for item in records] == [SECOND, FIRST]
    for item in records:
        assert all(expected[item['resident_id']][key] == value for key, value in item.items())
        assert 'workspace_path' not in item and 'watchboard' not in item
        assert item['backend'] == 'codex'
        assert item['lineage_id'] == LINEAGE_ID


def test_resident_reference_has_one_persisted_identity(tmp_path):
    """Resident 节点只以 node_id 持久化身份，移出画布或删除 Lineage 后身份记录仍保留"""
    store = LineageDataStore()
    store.put_lineage_definition(LINEAGE_ID, _lineage_definition(), {})
    for rid in (FIRST, SECOND):
        store.put_resident_record(rid, _record(rid))
    definition = _lineage_definition(
        nodes=[{"id": FIRST, "x": 12, "y": 34}, {"id": SECOND}],
        edges=[{"id": "edge_inherit", "source": FIRST, "target": SECOND, "type": "inheritance"}],
    )
    store.put_lineage_definition(LINEAGE_ID, definition, {})
    store.upsert_layout_positions(
        LINEAGE_ID, [{"object_id": FIRST, "x": 12, "y": 34}], updated_at="2026-10-11T05:00:00Z",
    )
    store.put_lineage_runtime(LINEAGE_ID, {"nodes": {FIRST: {"status": "idle"}, SECOND: {"status": "running"}}})
    graph = store.lineage_definition(LINEAGE_ID)
    assert {key: graph["nodes"][0][key] for key in ("id", "x", "y")} == {"id": FIRST, "x": 12.0, "y": 34.0}
    assert graph["edges"][0]["source"] == FIRST
    with store.database.read() as connection:
        assert lineage_data_schema_matches(connection)
        columns = [
            row[0]
            for row in connection.execute(
                'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position',
                ("lineage_nodes",),
            )
        ]
        assert "node_id" in columns and "resident_id" not in columns
        assert store.lineage_snapshot(LINEAGE_ID)[1]["nodes"] == {}
        row = connection.execute('SELECT node_id,node_type FROM lineage_nodes WHERE node_id=%s', (FIRST,)).fetchone()
        assert row["node_id"] == FIRST and row["node_type"] == "resident"
    assert _node_count(store) == 2
    graph["nodes"] = [graph["nodes"][0]]
    graph["edges"] = []
    store.put_lineage_definition(LINEAGE_ID, graph, {})
    assert store.lineage_snapshot(LINEAGE_ID)[1]["nodes"] == {}
    assert _node_count(store) == 2  # Removed canvas membership preserves the identity.
    store.remove_lineage(LINEAGE_ID, deleted_by="sample_admin")
    assert _node_count(store) == 2


def _node_count(store):
    with store.database.read() as connection:
        return connection.execute("SELECT COUNT(*) AS count FROM lineage_nodes WHERE lineage_id=%s", (LINEAGE_ID,)).fetchone()["count"]


@pytest.mark.parametrize("alias", ["node_id", "resident_id"])
def test_graph_reference_rejects_duplicate_identity_fields(tmp_path, alias):
    """Lineage 图节点另带 node_id 或 resident_id 字段时拒绝保存，定义保持不变"""
    store = LineageDataStore()
    store.put_lineage_definition(LINEAGE_ID, _lineage_definition(), {})
    for rid in (FIRST, SECOND):
        store.put_resident_record(rid, _record(rid))
    definition = _lineage_definition(nodes=[{"id": FIRST}])
    store.put_lineage_definition(LINEAGE_ID, definition, {})
    before = store.lineage_definition(LINEAGE_ID)
    definition["nodes"][0][alias] = FIRST
    with pytest.raises(ValueError, match="Unknown Resident graph projection fields"):
        store.put_lineage_definition(LINEAGE_ID, definition, {})
    assert store.lineage_definition(LINEAGE_ID) == before

"""Real PostgreSQL Resident membership reads stay bounded and live scoped.

:module: resource.identity
:importance: P1
:aspects: functional, architecture-conformance
:design: decision_0f57656a2acd
"""
from contextlib import contextmanager

import pytest

from management_common.postgres_database import PostgresConnection
from management_portal.society_service import business_fact_views as facts, lineage_data_residents, resource_client
from tests.cases.database_test_support import guard_queries
from tests.cases.test_canonical_resource_facts import canonical, R, L
from tests.cases.test_lineage_catalogs import _record


def test_queries_use_bounded_primary_keys_without_workspace_extensions_or_graph(canonical, monkeypatch):
    """归属查询按有上限的主键批次读取，不访问 Workspace 扩展或 Lineage 图表

    :aspects: functional, architecture-conformance, performance
    :design: decision_cf27d39e39cd
    :design-point:
    """
    state = canonical.state
    statements = []
    execute = PostgresConnection.execute
    def bounded(connection, query, parameters=(), **kwargs):
        assert '=ANY(%s)' in query and len(parameters) == 1 and len(parameters[0]) <= 500
        return execute(connection, query, parameters, **kwargs)
    monkeypatch.setattr(PostgresConnection, 'execute', bounded)
    snapshot = state.database.snapshot
    @contextmanager
    def guarded():
        with snapshot() as connection:
            yield guard_queries(connection, statements=statements, forbidden=(
                'lineage_edges', 'lineage_components', 'lineage_layout',
                'resident_workspace', 'families', 'family_residents', 'resident_runtime', 'select *'))
    monkeypatch.setattr(state.database, 'snapshot', guarded)
    monkeypatch.setattr(lineage_data_residents, 'residents_from_rows', lambda *a, **k: pytest.fail('full domain assembly'))
    for name in ('residents_by_id', 'lineage_definition'):
        monkeypatch.setattr(state, name, lambda *a, **k: pytest.fail('full domain assembly'))
    monkeypatch.setattr(resource_client, 'workspace_paths_many', lambda *a, **k: pytest.fail('Workspace RPC'))
    residents = [R, *[f'resident_{i:012x}' for i in range(1000)]]
    assert state.resident_usage_memberships(residents) == [{'resident_id': R, 'lineage_id': L}]
    assert len(statements) == 3
    assert all('join lineage_nodes' in query for query in statements)
    statements.clear()
    assert state.resident_usage_memberships([]) == []
    assert statements == []


def test_usage_memberships_keep_live_request_order_and_only_two_fields(canonical):
    """Usage 当前存活身份的轻量批量归属读取

    :module: workbench.notifications
    :aspects: functional, architecture-conformance, performance
    :design: decision_fc26f883773d
    :design-point:
    """
    state = canonical.state
    other, deleted = 'resident_eeeeeeeeeeee', 'resident_dddddddddddd'
    state.put_resident_record(other, _record(other, lineage_id=L))
    state.put_resident_record(deleted, _record(deleted, lineage_id=L))
    state.database.write(lambda c: c.execute("UPDATE residents SET deleted_at='2026-10-08T00:00:00Z' WHERE resident_id=%s", (deleted,)))
    assert state.resident_usage_memberships([other, deleted, R, other, 'resident_ffffffffffff']) == [
        {'resident_id': other, 'lineage_id': L}, {'resident_id': R, 'lineage_id': L}]


def test_unavailable_domain_fails_usage_reads_and_other_resources_pass_through(canonical, monkeypatch):
    """领域不可用时 Usage 归属读取明确报错，非 Family 资源投影原样通过

    :design: decision_cf27d39e39cd
    :design-point:
    """
    @contextmanager
    def unavailable():
        raise RuntimeError('Lineage data database does not match its schema')
        yield
    monkeypatch.setattr(canonical.state.database, 'snapshot', unavailable)
    with pytest.raises(RuntimeError, match='does not match its schema'):
        canonical.state.resident_usage_memberships([R])
    for kind, rid in (('resident', R), ('lineage', L), ('file', 'unrelated')):
        item = {'resource_type': kind, 'resource_id': rid, 'attributes': {'retained': True}}
        assert facts.project_resources([item])[0] is item

"""Resource metadata reads and retired-marker rejection preserve existing rows.

:design: decision_15aab18c0ab3
"""
from __future__ import annotations

import uuid

import pytest

from management_common.portal_database import portal_database
from management_portal import resource_index_backend as index
from management_portal.resources import registry_store as registry
from tests.cases.test_resource_index_backend import _record


@pytest.fixture(params=["registry", "index"])
def marker_store(request, monkeypatch):
    kind = request.param
    schema = "marker_" + uuid.uuid4().hex[:10]
    database = portal_database(schema=schema)
    if kind == "registry":
        monkeypatch.setattr(registry, "portal_database", lambda **kwargs: database)

        def reopen():
            store = registry.RegistryStore()
            store._ensure()
            return store

        table, suffix = "registry_meta", "resource-registry.v2"
        rows_table = "registry_documents"
    else:
        monkeypatch.setattr(index, "resource_database_schema", lambda: schema)
        reopen = index.IndexStore
        table, suffix = "resource_index_meta", "resource-index-store.v1"
        rows_table = "resource_index_records"
    return kind, database, reopen, table, suffix, rows_table


def snapshot(database, table):
    with database.snapshot() as connection:
        return [dict(row) for row in connection.execute(
            f"SELECT xmin::text AS row_version, * FROM {table} ORDER BY 2"
        ).fetchall()]


def marker(database, table):
    return next(row for row in snapshot(database, table) if row["key"] == "schema")


def test_new_store_writes_tfi_marker_and_reopen_is_read_only(marker_store):
    _kind, database, reopen, table, suffix, _rows = marker_store
    reopen()
    before = marker(database, table)
    assert before["value"] == "tfi." + suffix
    reopen()
    assert marker(database, table) == before


@pytest.mark.parametrize("namespace", ["archive", "tfi"])
def test_existing_marker_and_documents_keep_value_and_xmin(marker_store, namespace):
    """资源注册与索引只接受规范标记，重复读取或拒绝均保原值和事务版本"""
    kind, database, reopen, table, suffix, rows = marker_store
    store = reopen()
    if kind == "registry":
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO registry_documents VALUES ('fixture', 'original', 17, %s)",
                ('{"schema":"archive.fixture.v1","value":"unchanged"}',),
            )
    else:
        store.publish_snapshot("skill", [_record("skill_0123456789ab", "skill", adapter="skill")], {})
    with database.transaction() as connection:
        connection.execute(f"UPDATE {table} SET value=%s WHERE key='schema'", (namespace + "." + suffix,))
    before_marker, before_rows = marker(database, table), snapshot(database, rows)
    if namespace == 'archive':
        for _ in range(2):
            with pytest.raises(RuntimeError, match='schema'):
                reopen()
            assert marker(database, table) == before_marker
            assert snapshot(database, rows) == before_rows
        return
    reopened = reopen()
    assert marker(database, table) == before_marker
    assert snapshot(database, rows) == before_rows
    if kind == "index":
        assert reopened.internal_get("skill_0123456789ab")["resource_id"] == "skill_0123456789ab"
    reopen()
    assert marker(database, table) == before_marker
    assert snapshot(database, rows) == before_rows


@pytest.mark.parametrize("value", [
    "tfi.resource-registry.v1", "archive.resource-registry.v3",
    "tfi.resource-index-store.v2", "archive.resource-index-store.v0",
    "foreign.resource-index-store.v1", " tfi.resource-registry.v2", "7", "",
])
def test_wrong_name_version_or_prefix_is_rejected_without_rewrite(marker_store, value):
    _kind, database, reopen, table, _suffix, _rows = marker_store
    reopen()
    with database.transaction() as connection:
        connection.execute(f"UPDATE {table} SET value=%s WHERE key='schema'", (value,))
    before = marker(database, table)
    with pytest.raises(RuntimeError, match="schema"):
        reopen()
    assert marker(database, table) == before


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ

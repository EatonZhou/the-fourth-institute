"""Existing resource stores are used through their actual SQL contracts.

All SQL runs on the isolated PostgreSQL server selected by conftest. Neither
format labels nor unrelated schema extensions decide whether resources work.
"""
from concurrent.futures import ThreadPoolExecutor
from secrets import token_hex
from threading import Barrier
from types import SimpleNamespace

import pytest
from psycopg import errors, sql

from management_common.portal_database import portal_database
from management_portal import resource_index_store
from management_portal.resource_index_records import ChangeSet, resource_record
from management_portal.resources import registry_store


RID = "skill_0123456789ab"


@pytest.fixture(params=["registry", "index"])
def storage(request, monkeypatch):
    kind = request.param
    # The suite truncates rows between cases; schema-damage cases need their
    # own tables/sequence so a dropped column cannot leak to the next case.
    database = portal_database(schema="init_" + kind + "_" + token_hex(4))
    module = registry_store if kind == "registry" else resource_index_store
    monkeypatch.setattr(module, "portal_database", lambda **_kwargs: database)

    def open_store():
        if kind == "registry":
            store = registry_store.RegistryStore()
            store._ensure()
            return store
        return resource_index_store.IndexStore()

    def put(store, rid=RID, title="Original"):
        if kind == "registry":
            with store.writer("knowledge") as writer:
                writer.put("knowledge", rid, {"id": rid, "title": title})
        else:
            record = resource_record(
                resource_id=rid, resource_type="skill", adapter="test",
                title=title, summary="", path="/tmp/" + rid,
            )
            store.apply(ChangeSet("test", upserts=(record,)))

    def get(store, rid=RID):
        return store.get("knowledge", rid) if kind == "registry" else store.internal_get(rid)

    return SimpleNamespace(
        kind=kind, database=database, open=open_store, put=put, get=get,
        meta="registry_meta" if kind == "registry" else "resource_index_meta",
        records="registry_documents" if kind == "registry" else "resource_index_records",
        content="document_json" if kind == "registry" else "record_json",
        marker=registry_store.SCHEMA_VERSION if kind == "registry" else resource_index_store.RESOURCE_INDEX_STORE_SCHEMA,
    )


@pytest.mark.parametrize("marker", [None, "another-owner-format.v999"])
def test_existing_store_preserves_unknown_or_missing_label_and_extensions(storage, marker):
    storage.put(storage.open())
    with storage.database.transaction() as connection:
        connection.execute(sql.SQL("DELETE FROM {} WHERE key='schema'").format(sql.Identifier(storage.meta)))
        if marker is not None:
            connection.execute(sql.SQL("INSERT INTO {}(key,value) VALUES ('schema',%s)").format(
                sql.Identifier(storage.meta)), (marker,))
        connection.execute("CREATE TABLE unrelated_feature (value TEXT NOT NULL)")
        connection.execute("INSERT INTO unrelated_feature VALUES ('preserve')")
        connection.execute(sql.SQL("ALTER TABLE {} ADD COLUMN extra_data TEXT DEFAULT 'preserve'").format(
            sql.Identifier(storage.records)))

    reopened = storage.open()
    assert storage.get(reopened)["title"] == "Original"
    storage.put(reopened, title="Changed")
    assert storage.get(storage.open())["title"] == "Changed"
    with storage.database.read_current() as connection:
        row = connection.execute(sql.SQL("SELECT value FROM {} WHERE key='schema'").format(
            sql.Identifier(storage.meta))).fetchone()
        assert (row["value"] if row else None) == marker
        assert connection.execute("SELECT value FROM unrelated_feature").fetchone()["value"] == "preserve"
        assert connection.execute(sql.SQL("SELECT extra_data FROM {}").format(
            sql.Identifier(storage.records))).fetchone()["extra_data"] == "preserve"


def test_missing_unrelated_structure_does_not_block_resource_access(storage):
    storage.put(storage.open())
    relation = "deleted_identities" if storage.kind == "registry" else "resource_index_records_adapter_idx"
    command = "DROP TABLE {}" if storage.kind == "registry" else "DROP INDEX {}"
    with storage.database.transaction() as connection:
        connection.execute(sql.SQL(command).format(sql.Identifier(relation)))
    reopened = storage.open()
    assert storage.get(reopened)["title"] == "Original"
    storage.put(reopened, title="Changed")
    assert storage.get(storage.open())["title"] == "Changed"
    with storage.database.read_current() as connection:
        assert connection.execute("SELECT to_regclass(%s) AS relation", (relation,)).fetchone()["relation"] is None


@pytest.mark.parametrize("damage", ["column", "table"])
def test_missing_required_structure_reports_actual_sql_error_without_recreation(storage, damage):
    storage.put(storage.open())
    with storage.database.transaction() as connection:
        if damage == "column":
            connection.execute(sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
                sql.Identifier(storage.records), sql.Identifier(storage.content)))
        else:
            connection.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(storage.records)))
    error = errors.UndefinedColumn if damage == "column" else errors.UndefinedTable
    with pytest.raises(error, match=storage.content if damage == "column" else storage.records):
        storage.get(storage.open())
    with storage.database.read_current() as connection:
        if damage == "column":
            assert connection.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(
                sql.Identifier(storage.records))).fetchone()["n"] == 1
        else:
            assert connection.execute("SELECT to_regclass(%s) AS relation", (storage.records,)).fetchone()["relation"] is None


def test_empty_store_initialization_is_serialized_and_retains_concurrent_writes(storage):
    # Different instances/connections share only the database advisory lock.
    barrier = Barrier(4)

    def initialize(number):
        barrier.wait(timeout=5)
        store = storage.open()
        rid = f"skill_{number:012x}"
        storage.put(store, rid=rid, title=str(number))
        return rid

    with ThreadPoolExecutor(max_workers=4) as workers:
        ids = list(workers.map(initialize, range(4)))
    reopened = storage.open()
    assert [storage.get(reopened, rid)["title"] for rid in ids] == [str(n) for n in range(4)]
    with storage.database.read_current() as connection:
        assert connection.execute(sql.SQL("SELECT value FROM {} WHERE key='schema'").format(
            sql.Identifier(storage.meta))).fetchone()["value"] == storage.marker
        assert connection.execute(sql.SQL("SELECT value FROM {} WHERE key='directory_ownership_v1'").format(
            sql.Identifier(storage.meta))).fetchone()["value"] == "complete"


@pytest.mark.parametrize("storage", ["registry"], indirect=True)
def test_registry_reads_survive_missing_write_sequence_and_failed_write_rolls_back(storage):
    store = storage.open()
    with store.writer("knowledge") as writer:
        writer.put("knowledge", RID, {"title": "Original"})
    previous = store.revision("knowledge")
    with store.database.transaction() as connection:
        connection.execute("DROP SEQUENCE registry_revision_seq")
    reopened = storage.open()
    assert reopened.get("knowledge", RID) == {"title": "Original"}
    with pytest.raises(errors.UndefinedTable, match="registry_revision_seq"):
        with reopened.writer("knowledge") as writer:
            writer.put("knowledge", RID, {"title": "Changed"})
    assert reopened.get("knowledge", RID) == {"title": "Original"}
    assert reopened.revision("knowledge") == previous

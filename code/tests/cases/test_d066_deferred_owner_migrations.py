"""The D066 owner migrations select scope before opening payloads."""

import pytest

from management_common.directory_dependencies import prepare_write
from management_common.json_columns import json_text
from management_portal.society_service import (
    family_strategy_state, family_task_dispatch_storage, family_task_dispatches,
    instruction_name_migration, lineage_data_residents, lineage_lifecycle, lineage_resident_nodes, resident_catalog,
    resident_conversation_stream, resident_record_domain, resident_runtime_domain, resident_store,
)
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


DEFERRED = "lineage_7983b047d221"
DEFERRED_MESSAGE = "msg_" + "a" * 32
ACTIVE_MESSAGE = "msg_" + "b" * 32
ACTOR = "sample_admin"


@pytest.fixture
def lineage(future_creation_store):
    return lineage_lifecycle.create_lineage({"title": "Active", "project_id": "project_test"}, ACTOR)["lineage_id"]


def _resident(lineage_id, title):
    return lineage_resident_nodes.create_lineage_resident(lineage_id, {
        "title": title, "backend": "codex", "mode": "new_session",
    }, ACTOR)["resident"]["resident_id"]


def _seed_deferred(connection):
    prepare_write(connection)
    connection.execute("INSERT INTO lineages (lineage_id, title) VALUES (%s,%s)", (DEFERRED, "Deferred fixture"))


def _intents(connection):
    return connection.execute(
        "SELECT lineage_id,message_id,intent_json::text AS body FROM family_task_dispatches ORDER BY lineage_id,message_id"
    ).fetchall()


@pytest.fixture
def dispatches(lineage, request):
    store = resident_catalog.get_catalog().state_store
    with store.database.transaction() as connection:
        _seed_deferred(connection)
        family_task_dispatch_storage.create_schema(connection)
        connection.execute(
            "INSERT INTO family_task_dispatches VALUES (%s,%s,%s)",
            (DEFERRED, DEFERRED_MESSAGE, json_text({"private": "malformed deferred intent"})),
        )

    def cleanup():
        with store.database.transaction() as connection:
            connection.execute("DROP TABLE IF EXISTS family_task_dispatches")

    request.addfinalizer(cleanup)
    return store.database, lineage


@pytest.mark.parametrize("tombstone", [False, True], ids=["live", "tombstoned"])
def test_deferred_only_keeps_table_without_opening_readers(dispatches, monkeypatch, tombstone):
    database, _ = dispatches
    if tombstone:
        with database.transaction() as connection:
            prepare_write(connection)
            connection.execute("UPDATE lineages SET deleted_at=%s WHERE lineage_id=%s", ("2026-11-26T10:00:00Z", DEFERRED))

    def forbidden(*args):
        pytest.fail("Conversation was opened for the deferred lineage")

    monkeypatch.setattr(family_task_dispatches, "snapshot_dispatch_readers", forbidden)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", forbidden)
    with database.readonly_snapshot() as connection:
        before = _intents(connection)
        assert family_strategy_state.check_legacy_dispatches(connection) == []
    family_strategy_state.retire_legacy_dispatches()
    with database.readonly_snapshot() as connection:
        assert _intents(connection) == before


@pytest.mark.parametrize("blocked", [False, True], ids=["retire", "rollback"])
def test_mixed_intents_do_not_read_deferred_and_keep_other_conflicts(dispatches, monkeypatch, blocked):
    database, active = dispatches
    bad_lineage = "lineage_ffffffffffff"
    assert active < bad_lineage
    facade, target = _resident(active, "Facade"), _resident(active, "Steward")
    resident_conversation_stream.get_repository().set_input_state(target, ACTIVE_MESSAGE, "cancelled")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO family_task_dispatches VALUES (%s,%s,%s)",
            (active, ACTIVE_MESSAGE, json_text({"request": {
                "facade": facade, "target": target, "trigger": "review",
                "business_ref": "duty/family-review", "facts": {},
            }, "text": "Frozen review"})),
        )
        if blocked:
            prepare_write(connection)
            connection.execute("INSERT INTO lineages (lineage_id,title) VALUES (%s,%s)", (bad_lineage, "Blocked fixture"))
            connection.execute("INSERT INTO family_task_dispatches VALUES (%s,%s,%s)", (bad_lineage, ACTIVE_MESSAGE, "{}"))

    real_plan = family_task_dispatches.plan_legacy_dispatches

    def scoped_plan(connection, lineage_id, *readers):
        assert lineage_id != DEFERRED, "deferred intent payload was read"
        return real_plan(connection, lineage_id, *readers)

    monkeypatch.setattr(family_task_dispatches, "plan_legacy_dispatches", scoped_plan)
    with database.readonly_snapshot() as connection:
        before = _intents(connection)
        assert family_strategy_state.check_legacy_dispatches(connection) == ([{
            "kind": "legacy_family_dispatch", "lineage_id": bad_lineage, "message_id": ACTIVE_MESSAGE,
            "reason": "invalid_frozen_intent",
        }] if blocked else [])

    if blocked:
        with pytest.raises(ValueError, match="invalid_frozen_intent"):
            family_strategy_state.retire_legacy_dispatches()
        expected = before
    else:
        family_strategy_state.retire_legacy_dispatches()
        family_strategy_state.retire_legacy_dispatches()
        expected = [row for row in before if row["lineage_id"] == DEFERRED]
    with database.readonly_snapshot() as connection:
        assert _intents(connection) == expected


def test_runtime_migration_selects_durable_membership_before_hydration_and_file_reads(lineage, monkeypatch):
    catalog = resident_catalog.get_catalog()
    store = catalog.state_store
    permitted, deferred = _resident(lineage, "Permitted"), _resident(lineage, "Deferred")
    old = "RelayFamilyMessage"
    normalized = instruction_name_migration.normalize_prompt(old)
    assert normalized != old
    paths = {}
    for resident_id in (permitted, deferred):
        path = resident_record_domain.resident_instructions_path(catalog.get(resident_id))
        path.write_text(old, encoding="utf-8")
        paths[resident_id] = path
        settings = store.read_resident_execution_settings(resident_id)
        settings["prompt_context"] = {"system_prompt": old}
        store.write_resident_execution_settings(resident_id, settings)
    with store.database.transaction() as connection:
        _seed_deferred(connection)
        connection.execute("UPDATE lineage_nodes SET lineage_id=%s WHERE node_id=%s", (DEFERRED, deferred))

    real_hydrate = lineage_data_residents.residents_from_rows
    real_lock = resident_record_domain.state_lock
    real_read_settings = store.read_resident_execution_settings
    real_read_text = resident_store.read_text

    def hydrate(connection, rows):
        rows = list(rows)
        assert all(row["resident_id"] != deferred for row in rows), "deferred Resident was hydrated"
        return real_hydrate(connection, rows)

    def lock(resident_id):
        assert resident_id != deferred, "deferred Resident was locked"
        return real_lock(resident_id)

    def read_settings(resident_id):
        assert resident_id != deferred, "deferred runtime settings were read"
        return real_read_settings(resident_id)

    def read_text(path, **options):
        assert path != paths[deferred], "deferred Resident file was opened"
        return real_read_text(path, **options)

    monkeypatch.setattr(lineage_data_residents, "residents_from_rows", hydrate)
    monkeypatch.setattr(resident_record_domain, "state_lock", lock)
    monkeypatch.setattr(store, "read_resident_execution_settings", read_settings)
    monkeypatch.setattr(resident_store, "read_text", read_text)
    resident_runtime_domain.migrate_instruction_names()

    assert paths[permitted].read_text(encoding="utf-8") == normalized
    assert real_read_settings(permitted)["prompt_context"]["system_prompt"] == normalized
    assert paths[deferred].read_text(encoding="utf-8") == old
    assert real_read_settings(deferred)["prompt_context"]["system_prompt"] == old

"""Retire only the current Lineage automation identity; preserve historical facts."""
from __future__ import annotations

import copy
import importlib
import json
import runpy
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from management_portal.society_service import lineage_metadata_storage, state_store


LINEAGE = "lineage_aaaaaaaaaaaa"
LEADER = "resident_bbbbbbbbbbbb"
OLD_SCHEMA = "tfi.lineage-data.v62"
NEW_SCHEMA = "tfi.lineage-data.v63"
OLD_IDENTITY = {"type": "user", "id": "migration_fixture_user"}


@pytest.fixture
def legacy_database(monkeypatch, request):
    """A separate schema on pytest's disposable PostgreSQL, never the site DB."""
    database_module = importlib.import_module("management_common.portal_database")
    database = database_module.portal_database(schema="leader_migration_" + uuid4().hex[:8])

    def initialize(connection):
        state_store.create_lineage_data_schema(connection)
        # Existing v62 sites can retain TEXT although current declarations use JSONB.
        column_type = getattr(request, "param", "TEXT")
        assert column_type in {"TEXT", "JSON", "JSONB"}
        connection.execute(f"ALTER TABLE lineages ADD COLUMN run_as_principal_json {column_type}")
        connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (OLD_SCHEMA,))
        lineage_metadata_storage.put(connection, LINEAGE, {
            "lineage_membership": "formal", "project_id": "test_project", "title": "Migration",
            "description": "Keep ownership, history and progress", "leader_resident_id": LEADER,
            "created_by": "original_creator", "revision": 9, "layout_revision": 4,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "principal_hierarchy": {"level": "project", "scope_id": "test_project"},
        })
        connection.execute(
            "UPDATE lineages SET run_as_principal_json=%s, runtime_revision=27, "
            "runtime_updated_at='2026-01-03T00:00:00Z' WHERE lineage_id=%s",
            (json.dumps(OLD_IDENTITY), LINEAGE),
        )
        # Preserve both configured (including deleted) and unconfigured definitions.
        connection.execute(
            "INSERT INTO lineages(lineage_id,run_as_principal_json,deleted_at) "
            "VALUES ('lineage_cccccccccccc',%s,'2026-01-04T00:00:00Z'), "
            "('lineage_dddddddddddd',NULL,NULL), ('lineage_eeeeeeeeeeee','null',NULL)",
            (json.dumps(OLD_IDENTITY),),
        )
        accepted = {"event_type": "resident.completed", "actor": OLD_IDENTITY,
                    "payload": {"run_as_principal": OLD_IDENTITY, "resident_id": LEADER}}
        connection.execute(
            "INSERT INTO trigger_paths(trigger_path_id,lineage_id,root_kind,occurrence_id,root_node_id,"
            "cause_kind,cause_id,occurred_at,input_json,status,finished_at,revision) "
            "VALUES ('trigger_path_aaaaaaaaaaaa',%s,'event','event_aaaaaaaaaaaa','event_source_aaaaaaaaaaaa',"
            "'timer','tick-1','2026-01-03T00:00:00Z',%s,'completed','2026-01-03T00:00:01Z',7)",
            (LINEAGE, json.dumps(accepted)),
        )
        connection.execute(
            "INSERT INTO trigger_path_steps(step_id,trigger_path_id,node_id,kind,activation_key,status,"
            "created_at,finished_at,checkpoint_json,result_json) VALUES "
            "('trigger_step_aaaaaaaaaaaa','trigger_path_aaaaaaaaaaaa','effect_aaaaaaaaaaaa','effect',"
            "'activation-1','completed','2026-01-03T00:00:00Z','2026-01-03T00:00:01Z',%s,%s)",
            (json.dumps(accepted), json.dumps({"message_id": "msg_historical", "actor": OLD_IDENTITY})),
        )
        connection.execute(
            "INSERT INTO event_source_progress VALUES (%s,'event_source_aaaaaaaaaaaa',%s)",
            (LINEAGE, json.dumps({"cursor": 123, "next_due_at": "2026-01-05T00:00:00Z"})),
        )
        connection.execute(
            "INSERT INTO pending_event_deliveries VALUES (%s,'wire_aaaaaaaaaaaa','claim-1',0,%s)",
            (LINEAGE, json.dumps(accepted)),
        )
        connection.execute("INSERT INTO lineage_meta VALUES ('lineage_timer_lease','unchanged-lease')")

    database.write(initialize)
    monkeypatch.setattr(database_module, "portal_database", lambda *, schema: database)
    try:
        yield database
    finally:
        # Schema names are generated locally; no site object is selected for cleanup.
        database.write(lambda c: c.execute(f'DROP SCHEMA "{database.schema}" CASCADE'))


def snapshot(database):
    with database.snapshot() as connection:
        return {
            table: [row["value"] for row in connection.execute(
                f'SELECT to_jsonb(t) AS value FROM "{table}" t ORDER BY to_jsonb(t)::text'
            ).fetchall()]
            for table in state_store.POSTGRES_LINEAGE_DATA_COLUMNS
        }


def schema_marker(database):
    with database.read() as connection:
        return connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"]


def test_new_database_has_no_user_automation_identity_column():
    database = state_store.get_lineage_data_store().database
    with database.read() as connection:
        columns = {
            row["column_name"] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='lineages'"
            ).fetchall()
        }
        assert "run_as_principal_json" not in columns
        assert "leader_resident_id" in columns
        assert state_store.lineage_data_schema_matches(connection)
    assert "run_as_principal_json" not in lineage_metadata_storage.COLUMNS["lineages"]


@pytest.mark.parametrize("legacy_database", ["TEXT", "JSON", "JSONB"], indirect=True)
def test_migration_preview_apply_and_repeat_preserve_history(legacy_database):
    from scripts.migrate_lineage_leader_identity import migrate

    before = snapshot(legacy_database)
    preview = migrate()
    assert preview == {"previous_schema": OLD_SCHEMA, "schema": NEW_SCHEMA, "would_apply": True,
                       "retired_identity_count": 2, "applied": False, "changed": False}
    assert snapshot(legacy_database) == before
    assert OLD_IDENTITY["id"] not in json.dumps(preview)

    applied = migrate(apply=True)
    assert applied["changed"] is True and applied["applied"] is True
    assert applied["retired_identity_count"] == 2
    expected = copy.deepcopy(before)
    for row in expected["lineages"]:
        row.pop("run_as_principal_json")
    for row in expected["lineage_meta"]:
        if row["key"] == "schema":
            row["value"] = NEW_SCHEMA
    assert snapshot(legacy_database) == expected
    with legacy_database.read() as connection:
        assert state_store.lineage_data_schema_matches(connection)
        state_store.LineageDataStore._initialize(connection)
        header = lineage_metadata_storage.read_many(connection, [LINEAGE])[0]
        assert header["leader_resident_id"] == LEADER
        assert header["created_by"] == "original_creator"
        assert header["principal_hierarchy"]["level"] == "project"
        assert "run_as_principal" not in header
        marker_xmin = connection.execute("SELECT xmin::text AS xmin FROM lineage_meta WHERE key='schema'").fetchone()
    repeat = migrate(apply=True)
    assert repeat["previous_schema"] == NEW_SCHEMA
    assert repeat["changed"] is False and repeat["would_apply"] is False
    assert repeat["retired_identity_count"] == 0
    assert snapshot(legacy_database) == expected
    with legacy_database.read() as connection:
        assert connection.execute("SELECT xmin::text AS xmin FROM lineage_meta WHERE key='schema'").fetchone() == marker_xmin
    assert migrate()["would_apply"] is False


def test_post_ddl_validation_failure_rolls_back_drop_and_marker(legacy_database):
    from scripts.migrate_lineage_leader_identity import migrate

    legacy_database.write(lambda c: c.execute("ALTER TABLE lineages ADD COLUMN unexpected_field TEXT"))
    before = snapshot(legacy_database)
    with pytest.raises(ValueError, match="does not match"):
        migrate(apply=True)
    assert schema_marker(legacy_database) == OLD_SCHEMA
    assert snapshot(legacy_database) == before
    # The restored old column remains writable after the aborted DDL transaction.
    legacy_database.write(lambda c: c.execute("UPDATE lineages SET run_as_principal_json=run_as_principal_json"))


@pytest.mark.parametrize("version", ["tfi.lineage-data.v61", "tfi.lineage-data.v64", None])
def test_unsupported_or_missing_version_is_read_only(legacy_database, version):
    from scripts.migrate_lineage_leader_identity import migrate

    if version is None:
        legacy_database.write(lambda c: c.execute("DELETE FROM lineage_meta WHERE key='schema'"))
    else:
        legacy_database.write(lambda c: c.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (version,)))
    before = snapshot(legacy_database)
    for apply in (False, True):
        with pytest.raises(ValueError, match="Expected tfi.lineage-data.v62 or tfi.lineage-data.v63"):
            migrate(apply=apply)
        assert snapshot(legacy_database) == before


def test_retired_definition_field_is_rejected():
    from management_portal.society_service.lineage_definition_contract import validate_definition

    assert "run_as_principal" not in state_store.DEFINITION_COLUMN_FIELDS
    with pytest.raises(ValueError, match="unsupported fields.*run_as_principal"):
        validate_definition({"run_as_principal": OLD_IDENTITY})


def test_cli_requires_apply_and_emits_only_aggregate_identity_count(legacy_database, monkeypatch, capsys):
    from management_common import postgres_configuration

    monkeypatch.setattr(postgres_configuration, "host_database_url", lambda: None)
    script = Path(__file__).resolve().parents[2] / "scripts" / "migrate_lineage_leader_identity.py"
    before = snapshot(legacy_database)
    monkeypatch.setattr(sys, "argv", [str(script)])
    runpy.run_path(str(script), run_name="__main__")
    output = capsys.readouterr().out
    assert json.loads(output)["applied"] is False
    assert OLD_IDENTITY["id"] not in output
    assert snapshot(legacy_database) == before
    monkeypatch.setattr(sys, "argv", [str(script), "--apply"])
    runpy.run_path(str(script), run_name="__main__")
    output = capsys.readouterr().out
    assert json.loads(output)["applied"] is True
    assert OLD_IDENTITY["id"] not in output
    assert schema_marker(legacy_database) == NEW_SCHEMA

"""Existing execution storage is usable without global schema admission.

All mutations below are confined to the test's isolated PostgreSQL namespace.
"""
import os
import uuid
from types import SimpleNamespace

import pytest
from psycopg.errors import UndefinedColumn, UndefinedTable

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.harness_effectiveness import HarnessEffectivenessStore
from tests.cases.database_test_support import connect


@pytest.fixture(autouse=True)
def isolated_storage_namespace(monkeypatch):
    # The default fixture truncates rows while preserving schema across cases.
    # Structure mutation probes need distinct schemas, not shared table shapes.
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE",
                       os.environ["PORTAL_DATABASE_NAMESPACE"][:24] + "_" + uuid.uuid4().hex[:12])


@pytest.mark.parametrize("change", [
    "DELETE FROM agent_store_meta WHERE key='schema'",
    "UPDATE agent_store_meta SET value='tfi.agent-data.v999' WHERE key='schema'",
    "DROP TABLE agent_store_meta",
    "ALTER TABLE agents ADD COLUMN future_optional TEXT",
    "DROP TABLE operation_positions",
    "ALTER TABLE executions DROP COLUMN provider_turn_id",
])
def test_agent_existing_storage_does_not_gate_unrelated_operations(change):
    first = AgentDataStore()
    expected = first.ensure_agent("resident_schema_survivor")
    with connect(first) as connection:
        connection.execute(change)
    reopened = AgentDataStore()
    actual = reopened.ensure_agent("resident_schema_survivor")
    assert {key: actual[key] for key in expected} == expected
    assert reopened.ensure_agent("resident_schema_new")["agent_id"] == "resident_schema_new"
    # Initialization must not rewrite a metadata row, fill a missing column or
    # repair a table as a side effect of another feature's successful access.
    with connect(reopened) as connection:
        if change.startswith("DROP TABLE"):
            table = change.split()[-1]
            assert connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None
        elif change.startswith("UPDATE"):
            assert connection.execute("SELECT value FROM agent_store_meta WHERE key='schema'").fetchone()[0] == "tfi.agent-data.v999"
        elif change.startswith("DELETE"):
            assert connection.execute("SELECT value FROM agent_store_meta WHERE key='schema'").fetchone() is None
        elif change.startswith("ALTER TABLE executions"):
            with pytest.raises(UndefinedColumn, match="provider_turn_id"):
                connection.execute("SELECT provider_turn_id FROM executions")


def test_agent_missing_core_table_fails_at_its_actual_operation():
    first = AgentDataStore()
    with connect(first) as connection:
        connection.execute("DROP TABLE agents")
    reopened = AgentDataStore()
    with pytest.raises(UndefinedTable, match="agents"):
        reopened.ensure_agent("resident_needs_missing_table")


def test_agent_completely_empty_schema_is_created():
    store = AgentDataStore()
    assert store.ensure_agent("resident_new_database")["agent_id"] == "resident_new_database"
    with connect(store) as connection:
        assert connection.execute("SELECT value FROM agent_store_meta WHERE key='schema'").fetchone()[0] == "tfi.agent-data.v18"
        assert connection.execute("SELECT to_regclass('execution_inputs')").fetchone()[0]


@pytest.mark.parametrize("change,expected", [
    ("DROP TABLE agent_store_meta", ""),
    ("DELETE FROM agent_store_meta WHERE key='schema'", ""),
    ("UPDATE agent_store_meta SET value='other.implementation' WHERE key='schema'", "other.implementation"),
])
def test_execution_health_accepts_absent_or_descriptive_agent_metadata(change, expected):
    from management_portal.execution.service_rpc import health

    store = AgentDataStore()
    with connect(store) as connection:
        connection.execute(change)
    server = SimpleNamespace(agent_data=store, instance="test", build_id="test",
                             scheduler=SimpleNamespace(health=lambda: {}),
                             notification_publisher=SimpleNamespace(health=lambda: {}))
    assert store.health()["schema"] == expected
    snapshot = health(server, {})
    assert snapshot["ok"] is True
    assert snapshot["agent_data"]["schema"] == expected
    assert store.ensure_agent("resident_health_still_usable")["agent_id"] == "resident_health_still_usable"


def test_agent_health_does_not_hide_damaged_metadata_columns():
    store = AgentDataStore()
    with connect(store) as connection:
        connection.execute("ALTER TABLE agent_store_meta DROP COLUMN value")
    with pytest.raises(UndefinedColumn, match="value"):
        store.health()


@pytest.mark.parametrize("change", [
    "ALTER TABLE harness_strategy_observations ADD COLUMN future_optional TEXT",
    "CREATE TABLE future_feature (id TEXT)",
    "DROP TABLE harness_quality_labels CASCADE",
    "DROP VIEW harness_observations",
    "ALTER TABLE harness_strategy_observations DROP COLUMN provider_latency_observed CASCADE",
])
def test_harness_existing_schema_does_not_gate_unrelated_queries(change):
    first = HarnessEffectivenessStore()
    try:
        with first._connect():
            pass
        with connect(first) as connection:
            connection.execute(change)
    finally:
        first.close()
    reopened = HarnessEffectivenessStore()
    try:
        with reopened._connect() as connection:
            assert connection.execute("SELECT count(*) FROM harness_strategy_observations").fetchone()[0] == 0
            if change.startswith("DROP TABLE"):
                with pytest.raises(UndefinedTable, match="harness_quality_labels"):
                    connection.execute("SELECT * FROM harness_quality_labels")
            elif change.startswith("DROP VIEW"):
                with pytest.raises(UndefinedTable, match="harness_observations"):
                    connection.execute("SELECT * FROM harness_observations")
            elif change.startswith("ALTER TABLE") and "DROP COLUMN" in change:
                with pytest.raises(UndefinedColumn, match="provider_latency_observed"):
                    connection.execute("SELECT provider_latency_observed FROM harness_strategy_observations")
    finally:
        reopened.close()


def test_harness_empty_schema_is_created_and_core_absence_remains_local():
    first = HarnessEffectivenessStore()
    try:
        with first._connect() as connection:
            assert connection.execute("SELECT to_regclass('harness_observations')").fetchone()[0]
        with connect(first) as connection:
            connection.execute("DROP TABLE harness_strategy_observations CASCADE")
    finally:
        first.close()
    reopened = HarnessEffectivenessStore()
    try:
        with reopened._connect() as connection:
            assert connection.execute("SELECT count(*) FROM harness_quality_labels").fetchone()[0] == 0
            with pytest.raises(UndefinedTable, match="harness_strategy_observations"):
                connection.execute("SELECT * FROM harness_strategy_observations")
    finally:
        reopened.close()


@pytest.mark.parametrize("change", [
    "DELETE FROM execution_store_meta WHERE key='schema_version'",
    "UPDATE execution_store_meta SET value='999' WHERE key='schema_version'",
    "DROP TABLE execution_store_meta",
    "ALTER TABLE executions ADD COLUMN future_optional TEXT",
    "DROP INDEX executions_schedule_idx",
    "DROP TABLE execution_input_controls",
    "ALTER TABLE executions DROP COLUMN usage_accounted_json",
])
def test_scheduler_existing_storage_opens_without_repair_or_metadata_gate(change):
    from management_portal.execution.scheduler import Scheduler

    first = Scheduler()
    with connect(first) as connection:
        connection.execute(change)
        before = connection.execute(
            "SELECT oid, tgname, tgenabled FROM pg_trigger "
            "WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace=current_schema()::regnamespace) "
            "AND NOT tgisinternal ORDER BY tgname"
        ).fetchall()
    reopened = Scheduler()
    assert reopened.latest_runtime_root(["unknown-binding"]) == ""
    with connect(reopened) as connection:
        after = connection.execute(
            "SELECT oid, tgname, tgenabled FROM pg_trigger "
            "WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace=current_schema()::regnamespace) "
            "AND NOT tgisinternal ORDER BY tgname"
        ).fetchall()
        assert after == before
        if change.startswith("DROP TABLE"):
            table = change.split()[-1]
            assert connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None
        elif change.startswith("DELETE"):
            assert connection.execute("SELECT value FROM execution_store_meta WHERE key='schema_version'").fetchone() is None
        elif change.startswith("UPDATE"):
            assert connection.execute("SELECT value FROM execution_store_meta WHERE key='schema_version'").fetchone()[0] == "999"
        elif change.startswith("DROP INDEX"):
            assert connection.execute("SELECT to_regclass('executions_schedule_idx')").fetchone()[0] is None
        elif "DROP COLUMN" in change:
            with pytest.raises(UndefinedColumn, match="usage_accounted_json"):
                connection.execute("SELECT usage_accounted_json FROM executions")
    if change == "DROP TABLE execution_input_controls":
        with pytest.raises(UndefinedTable, match="execution_input_controls"):
            reopened.input_control("unknown-control")


@pytest.mark.parametrize("change", [
    "DROP TRIGGER executions_scheduler_change ON executions",
    "ALTER TABLE executions DISABLE TRIGGER executions_scheduler_change",
])
def test_scheduler_does_not_install_or_reenable_existing_notifications(change):
    from management_portal.execution.scheduler import Scheduler

    first = Scheduler()
    with connect(first) as connection:
        # A fresh schema still has its normal notification triggers.
        assert connection.execute("SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace=current_schema()::regnamespace) "
            "AND NOT tgisinternal").fetchone()[0] == 5
        connection.execute(change)
    reopened = Scheduler()
    assert reopened.latest_runtime_root(["unknown-binding"]) == ""
    with connect(reopened) as connection:
        trigger = connection.execute("SELECT tgenabled FROM pg_trigger "
            "WHERE tgrelid='executions'::regclass AND tgname='executions_scheduler_change'").fetchone()
        assert trigger is None if change.startswith("DROP") else trigger[0] == "D"


def test_scheduler_first_install_after_execution_settings_preserves_settings():
    from management_portal.execution.scheduler import Scheduler
    from management_portal.execution.service_settings import ExecutionSettings

    settings = ExecutionSettings()
    settings.update(False)
    before = settings.get()
    scheduler = Scheduler()
    assert scheduler.latest_runtime_root(["unknown-binding"]) == ""
    assert ExecutionSettings().get() == before
    with connect(scheduler) as connection:
        assert connection.execute(
            "SELECT value FROM execution_store_meta WHERE key='schema_version'"
        ).fetchone()[0] == "19"
        assert connection.execute("SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace=current_schema()::regnamespace) "
            "AND NOT tgisinternal").fetchone()[0] == 5


@pytest.mark.parametrize("state", ["core_missing", "meta_only", "workers_only"])
def test_scheduler_missing_core_fails_only_on_actual_access(state):
    from management_common.portal_database import portal_database
    from management_portal.execution.scheduler import Scheduler

    if state == "core_missing":
        first = Scheduler()
        with connect(first) as connection:
            connection.execute("DROP TABLE executions CASCADE")
    else:
        portal_database(schema="execution_runtime").write(
            lambda c: c.execute(
                "CREATE TABLE execution_store_meta (key TEXT PRIMARY KEY)" if state == "meta_only"
                else "CREATE TABLE execution_workers (worker_id TEXT PRIMARY KEY)"))
    reopened = Scheduler()
    with connect(reopened) as connection:
        assert connection.execute("SELECT to_regclass('executions')").fetchone()[0] is None
        if state != "core_missing":
            assert connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema()"
            ).fetchone()[0] == 1
    with pytest.raises(UndefinedTable, match="executions"):
        reopened.latest_runtime_root(["unknown-binding"])

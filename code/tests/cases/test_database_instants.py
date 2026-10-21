"""Time cutover preserves instants, nullable state, indexes and read-only views."""
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import psycopg
import pytest
from psycopg import sql

from scripts.migrate_database_instants import migrate


def connection():
    return psycopg.connect(os.environ["PORTAL_DATABASE_URL"])


@pytest.fixture
def migration_namespace():
    namespace = os.environ["PORTAL_DATABASE_NAMESPACE"] + "_instant_" + uuid.uuid4().hex[:8]
    yield namespace
    with connection() as c:
        rows = c.execute("SELECT nspname FROM pg_namespace WHERE starts_with(nspname,%s)", (namespace + "_",)).fetchall()
        for (name,) in rows:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def create(c, owner, ddl, namespace):
    name = namespace + "_" + owner
    c.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(name)))
    c.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(name)))
    c.execute(ddl)
    return name


def test_sequence_time_rewrite_preserves_only_current_chat_markers(migration_namespace):
    """序列时间改写仅更新原本有效的 Chat 同步标记"""
    with connection() as c:
        schema = create(c, 'agent', 'CREATE TABLE sequences (sequence_id text PRIMARY KEY, created_at text, updated_at text)', migration_namespace)
        c.execute("CREATE TABLE chat_message_positions (sequence_id text, source_kind text, source_id text, message_id text)")
        c.execute("INSERT INTO sequences VALUES ('fresh','2026-10-15T00:00:00Z','2026-10-15T00:00:00Z'),('stale','2026-10-15T00:00:00Z','2026-10-15T00:00:00Z')")
        c.execute("INSERT INTO chat_message_positions SELECT sequence_id,'sync','__sync__',CASE WHEN sequence_id='fresh' THEN xmin::text ELSE 'old' END FROM sequences")
    with connection() as c:
        migrate(c, namespace=migration_namespace, tables=['agent.sequences'])
        c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        values = dict(c.execute("SELECT s.sequence_id,p.message_id=s.xmin::text FROM sequences s JOIN chat_message_positions p USING(sequence_id)"))
        assert values == {'fresh': True, 'stale': False}


def test_time_migration_preserves_offset_fraction_and_pending_states(migration_namespace):
    """时间迁移保留时区小数精度待执行空值及索引"""
    with connection() as c:
        name = create(c, "agent", """CREATE TABLE executions (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE INDEX execution_created ON executions(created_at);
            INSERT INTO executions VALUES
                ('pending','','','2026-10-14T15:00:00.123456+08:00'),
                ('finished','2026-10-14T07:01:00Z','2026-10-14T07:02:00Z','2026-10-14T07:00:00.123456Z')""", migration_namespace)
        result = migrate(c, namespace=migration_namespace, tables=["agent.executions"])
        rows = c.execute(sql.SQL("SELECT id,created_at,started_at,finished_at FROM {}.executions ORDER BY id").format(sql.Identifier(name))).fetchall()
        assert result["agent.executions"]["rows"] == 2
        assert rows[0][1] == rows[1][1] == datetime(2026, 10, 14, 7, 0, 0, 123456, timezone.utc)
        assert rows[1][2:] == (None, None)
        assert rows[0][3] > rows[0][2]
        assert c.execute("SELECT to_regclass(%s)", (name + ".execution_created",)).fetchone()[0]
        assert migrate(c, namespace=migration_namespace, tables=["agent.executions"]) == result


def test_zero_absence_is_distinct_from_epoch_dispatch_and_identity_seconds(migration_namespace):
    """时间迁移区分缺失零值与调度纪元并保留身份秒数"""
    with connection() as c:
        name = create(c, "portal_identity", """CREATE TABLE passkey_credentials (
            id TEXT PRIMARY KEY, created_at BIGINT NOT NULL, last_used_at BIGINT NOT NULL);
            INSERT INTO passkey_credentials VALUES ('key',1787807967,0)""", migration_namespace)
        migrate(c, namespace=migration_namespace, tables=["portal_identity.passkey_credentials"])
        assert c.execute(sql.SQL("SELECT created_at,last_used_at FROM {}.passkey_credentials").format(sql.Identifier(name))).fetchone() == (
            datetime.fromtimestamp(1787807967, timezone.utc), None)
        name = create(c, "execution_runtime", """CREATE TABLE capacity_dispatch_state (
            id TEXT PRIMARY KEY, last_dispatched_at DOUBLE PRECISION NOT NULL DEFAULT 0,
            next_dispatch_at DOUBLE PRECISION NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            INSERT INTO capacity_dispatch_state VALUES ('c',0,1791960948.125,'2026-10-14T00:00:00Z')""", migration_namespace)
        migrate(c, namespace=migration_namespace, tables=["execution_runtime.capacity_dispatch_state"])
        assert c.execute(sql.SQL("SELECT extract(epoch FROM next_dispatch_at-last_dispatched_at) FROM {}.capacity_dispatch_state").format(sql.Identifier(name))).fetchone()[0] == 1791960948.125
        c.execute(sql.SQL("INSERT INTO {}.capacity_dispatch_state(id,updated_at) VALUES ('d',now())").format(sql.Identifier(name)))
        assert c.execute(sql.SQL("SELECT last_dispatched_at=TIMESTAMPTZ 'epoch' FROM {}.capacity_dispatch_state WHERE id='d'").format(sql.Identifier(name))).fetchone()[0]


def test_invalid_time_aborts_without_changing_schema_or_records(migration_namespace):
    """无时区时间使迁移中止且保留原字段类型与记录"""
    with connection() as c:
        name = create(c, "agent", """CREATE TABLE agents (id TEXT PRIMARY KEY,updated_at TEXT);
            INSERT INTO agents VALUES ('bad','2026-10-14T00:00:00')""", migration_namespace)
        with pytest.raises(ValueError, match="lack a timezone"), c.transaction():
            migrate(c, namespace=migration_namespace, tables=["agent.agents"])
        assert c.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='agents' AND column_name='updated_at'", (name,)).fetchone()[0] == "text"
        assert c.execute(sql.SQL("SELECT updated_at FROM {}.agents").format(sql.Identifier(name))).fetchone()[0] == "2026-10-14T00:00:00"


def test_harness_observations_remains_a_join_view_after_time_cutover(migration_namespace):
    """时间迁移后 Harness 观测仍保持连接视图及标签内容"""
    from management_portal.execution.harness_effectiveness import HARNESS_TABLE_COLUMNS, create_observation_view
    with connection() as c:
        name = create(c, "harness_effectiveness", "SELECT 1", migration_namespace)
        for table, columns in HARNESS_TABLE_COLUMNS.items():
            ddl = [sql.SQL("{} {}").format(sql.Identifier(col), sql.SQL("DOUBLE PRECISION" if col in {"recorded_at", "updated_at"} else "TEXT")) for col in sorted(columns)]
            c.execute(sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(table), sql.SQL(",").join(ddl)))
        c.execute("INSERT INTO harness_strategy_observations(execution_id,load_family,recorded_at) VALUES ('e','normal',1791960948)")
        c.execute("INSERT INTO harness_quality_labels(execution_id,load_family,quality_result,updated_at) VALUES ('e','normal','pass',1791960948)")
        create_observation_view(c)
        migrate(c, namespace=migration_namespace, tables=[
            "harness_effectiveness.harness_quality_labels", "harness_effectiveness.harness_strategy_observations"])
        assert c.execute("SELECT relkind FROM pg_class WHERE oid=%s::regclass", (name + ".harness_observations",)).fetchone()[0] == "v"
        row = c.execute("SELECT recorded_at,quality_result FROM harness_observations").fetchone()
        assert row == (datetime.fromtimestamp(1791960948, timezone.utc), "pass")


def test_paper_searches_do_not_trim_at_a_thousand():
    """论文检索记录超过一千条时仍完整保留"""
    from management_portal.semantic_search_store import PAPER_SEARCHES
    with patch("management_portal.semantic_search_store._notify"):
        with PAPER_SEARCHES.locked():
            for i in range(1005):
                PAPER_SEARCHES.save({"search_id": f"search-{i}", "status": "running", "created_at": 1700000000 + i})
    assert len(PAPER_SEARCHES.load()) == 1005
    assert PAPER_SEARCHES.get("search-0")["status"] == "running"


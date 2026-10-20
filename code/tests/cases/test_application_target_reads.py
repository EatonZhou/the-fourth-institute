"""Application configuration readers remain usable across owner migrations."""
import secrets

import pytest

from management_common.portal_database import portal_database
from management_portal import application_targets as targets


@pytest.fixture
def configuration(monkeypatch):
    database = portal_database(schema="application_target_reader_" + secrets.token_hex(6))
    monkeypatch.setattr(targets, "portal_database", lambda **_kwargs: database)
    with database.transaction() as connection:
        connection.executescript("""
            CREATE TABLE lineage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO lineage_meta VALUES ('schema', 'tfi.lineage-data.v999');
            CREATE TABLE lineages (lineage_id TEXT PRIMARY KEY, deleted_at TEXT);
            CREATE TABLE residents (resident_id TEXT PRIMARY KEY, deleted_at TEXT, future_owner_column TEXT);
            CREATE TABLE lineage_nodes (node_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL
                REFERENCES lineages(lineage_id), node_type TEXT NOT NULL, deleted_at TEXT);
            INSERT INTO lineages VALUES ('lineage_111111111111', NULL), ('lineage_222222222222', NULL);
            INSERT INTO residents VALUES ('resident_111111111111', NULL, NULL);
            INSERT INTO lineage_nodes VALUES ('resident_111111111111', 'lineage_111111111111', 'resident', NULL);
        """)
        targets.create_schema(connection)
        connection.executescript("""
            INSERT INTO application_targets (application,lineage_id) VALUES ('tau', 'lineage_111111111111');
            INSERT INTO application_targets (application,resident_id) VALUES ('resident_discovery', 'resident_111111111111');
            INSERT INTO application_targets (application,resident_id) VALUES ('semantic_search:knowledge', 'resident_111111111111');
            INSERT INTO application_targets (application,lineage_id) VALUES ('resource_chat:project', 'lineage_111111111111');
        """)
    return database


def test_readers_use_their_columns_without_weakening_owner_schema_checks(configuration):
    """Lineage schema 版本不符时应用目标仍可按自身列读取，schema 检查照常报错"""
    from management_portal.society_service.state_store import LineageDataStore

    with pytest.raises(RuntimeError, match="does not match its schema"):
        with configuration.transaction() as connection:
            LineageDataStore._initialize(connection)
    assert targets.lineage("tau") == "lineage_111111111111"
    assert targets.resource_chat_lineage("project") == "lineage_111111111111"
    expected = {"resident_id": "resident_111111111111", "lineage_id": "lineage_111111111111"}
    assert targets.discovery_resident() == expected
    assert targets.search_resident("knowledge") == expected
    with configuration.read() as connection:
        assert connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"] == "tfi.lineage-data.v999"


def test_resident_target_reports_its_current_lineage(configuration):
    """Resident 应用目标返回该 Resident 当前所在的 Lineage"""
    with configuration.transaction() as connection:
        connection.execute("UPDATE lineage_nodes SET lineage_id='lineage_222222222222'")
    assert targets.discovery_resident() == {
        "resident_id": "resident_111111111111", "lineage_id": "lineage_222222222222",
    }
    assert targets.search_resident("knowledge")["lineage_id"] == "lineage_222222222222"


@pytest.mark.parametrize("reason", ["missing", "deleted", "deleted_lineage"])
def test_resident_target_requires_live_membership(configuration, reason):
    """Resident 目标的 Lineage 成员关系缺失或已删除时视为未配置"""
    with configuration.transaction() as connection:
        if reason == "missing":
            connection.execute("DELETE FROM lineage_nodes")
        elif reason == "deleted":
            connection.execute("UPDATE lineage_nodes SET deleted_at='2026-10-13T00:00:00Z'")
        else:
            connection.execute("UPDATE lineages SET deleted_at='2026-10-13T00:00:00Z'")
    with pytest.raises(targets.ApplicationUnconfigured):
        targets.discovery_resident()


@pytest.mark.parametrize("kind", ["lineage", "resident"])
@pytest.mark.parametrize("reason", ["unconfigured", "deleted"])
def test_missing_and_deleted_targets_still_fail_explicitly(configuration, kind, reason):
    """未配置或已删除的 Lineage、Resident 目标在读取时明确报未配置"""
    with configuration.transaction() as connection:
        if reason == "unconfigured":
            application = "tau" if kind == "lineage" else "resident_discovery"
            connection.execute("DELETE FROM application_targets WHERE application=%s", (application,))
        else:
            table = "lineages" if kind == "lineage" else "residents"
            connection.execute(f"UPDATE {table} SET deleted_at='2026-10-13T00:00:00Z'")
    with pytest.raises(targets.ApplicationUnconfigured):
        (targets.lineage("tau") if kind == "lineage" else targets.discovery_resident())


def test_application_reads_the_target_kind_it_declares(configuration):
    """应用只按自己声明的目标类型读取：Lineage 应用不能当作 Resident 读取，未知应用直接拒绝"""
    with pytest.raises(ValueError, match="does not give its work to a resident"):
        targets.resident("tau")
    with pytest.raises(ValueError, match="does not give its work to a lineage"):
        targets.lineage("resident_discovery")
    with pytest.raises(ValueError, match="Unknown application"):
        targets.lineage("discussion_reply")

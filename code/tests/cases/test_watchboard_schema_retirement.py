"""Watchboard retirement requires the replacement view and commits as one transaction."""
import json
import pytest
from management_common.portal_database import portal_database
from scripts.migrate_watchboard_storage import (
    OLD_SCHEMA, NEW_SCHEMA, TELEPUS_RESIDENT, VIEW_KEY, migrate, validate_display,
)


@pytest.fixture
def database():
    db = portal_database(schema='watchboard_retirement')
    with db.transaction() as c:
        c.execute('CREATE TABLE lineage_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute("INSERT INTO lineage_meta VALUES ('schema',%s)", (OLD_SCHEMA,))
        c.execute('CREATE TABLE resident_watchboards (resident_id TEXT PRIMARY KEY)')
        c.execute('INSERT INTO resident_watchboards VALUES (%s)', (TELEPUS_RESIDENT,))
        c.execute('CREATE TABLE lineage_nodes (node_id TEXT PRIMARY KEY,lineage_id TEXT,node_type TEXT,deleted_at TEXT)')
        c.execute("INSERT INTO lineage_nodes VALUES (%s,'lineage_telepus','resident',NULL),('view','lineage_telepus','event_source',NULL)", (TELEPUS_RESIDENT,))
        c.execute('CREATE TABLE lineage_components (node_id TEXT PRIMARY KEY,client_key TEXT)')
        c.execute('INSERT INTO lineage_components VALUES (%s,%s)', ('view', VIEW_KEY))
    yield db
    with db.transaction() as c:
        c.execute('DROP TABLE IF EXISTS lineage_components,lineage_nodes,resident_watchboards,lineage_meta')


def test_retirement_preserves_lineage_and_is_idempotent(database):
    """退役 Watchboard 表时删表并升级 schema 版本，不改 Lineage 节点，重复执行为空操作"""
    with database.transaction() as c:
        before = [dict(row) for row in c.execute('SELECT * FROM lineage_nodes ORDER BY node_id').fetchall()]
        assert migrate(c)['telepus_view_id'] == 'view'
        assert c.execute("SELECT to_regclass('resident_watchboards') AS name").fetchone()['name'] is None
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()['value'] == NEW_SCHEMA
        assert [dict(row) for row in c.execute('SELECT * FROM lineage_nodes ORDER BY node_id').fetchall()] == before
        assert migrate(c)['already_applied']


@pytest.mark.parametrize('state', ['missing', 'deleted', 'other-lineage'])
def test_retirement_requires_live_view_in_the_same_lineage(database, state):
    """Telepus 视图组件缺失、已删除或属于其他 Lineage 时拒绝退役 Watchboard 表"""
    with database.transaction() as c:
        if state == 'missing':
            c.execute('DELETE FROM lineage_components')
        elif state == 'deleted':
            c.execute("UPDATE lineage_nodes SET deleted_at='now' WHERE node_id='view'")
        else:
            c.execute("UPDATE lineage_nodes SET lineage_id='other' WHERE node_id='view'")
        with pytest.raises(ValueError, match='Migrate Telepus'):
            migrate(c)
        assert c.execute('SELECT resident_id FROM resident_watchboards').fetchone()['resident_id'] == TELEPUS_RESIDENT
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()['value'] == OLD_SCHEMA


def test_retirement_rolls_back_ddl_with_schema_version(database):
    """Watchboard 退役事务中断时删表与 schema 版本一并回滚，版本不符时拒绝迁移"""
    with pytest.raises(RuntimeError), database.transaction() as c:
        migrate(c)
        raise RuntimeError('cutover interrupted')
    with database.transaction() as c:
        assert c.execute('SELECT resident_id FROM resident_watchboards').fetchone()['resident_id'] == TELEPUS_RESIDENT
        assert c.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()['value'] == OLD_SCHEMA
        c.execute("UPDATE lineage_meta SET value='unexpected'")
        with pytest.raises(ValueError, match='Expected'):
            migrate(c)


def test_display_validation_excludes_bridge_commands_and_config(tmp_path):
    """Watchboard 显示目录校验拒绝 agent.yaml 中仍保留脚本命令"""
    (tmp_path / 'watchboard.yaml').write_text('title: Telepus\n')
    path = tmp_path / 'agent.yaml'
    path.write_text(json.dumps({'commands': {}, 'configs': []}))
    validate_display(tmp_path)
    path.write_text(json.dumps({'commands': {'router-dispatch': {}}, 'configs': []}))
    with pytest.raises(ValueError, match='no script commands'):
        validate_display(tmp_path)

"""Terminal maintenance batches deletions without changing retention authority.

:module: execution.service
:importance: P1
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import secrets
from types import SimpleNamespace

import pytest
from management_common.postgres_database import PostgresDatabase
from management_portal.execution.scheduler_queue import ExecutionQueue


@pytest.fixture
def pruning():
    schema = os.environ['PORTAL_DATABASE_NAMESPACE'] + '_prune_' + secrets.token_hex(3)
    db = PostgresDatabase(os.environ['PORTAL_DATABASE_URL'], schema=schema)
    with db.connect() as c:
        c.execute('CREATE TABLE executions(execution_id TEXT PRIMARY KEY,status TEXT,acknowledged_at TIMESTAMPTZ,updated_at TIMESTAMPTZ)')
        c.execute('CREATE TABLE execution_store_meta(key TEXT PRIMARY KEY,value TEXT)')
        c.execute('CREATE TABLE child(execution_id TEXT REFERENCES executions ON DELETE CASCADE)')
    statements, forgotten, notifications = [], [], []

    class Observed:
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, *args):
            statements.append(' '.join(sql.split()))
            return self.connection.execute(sql, *args)
        def executemany(self, *args):
            raise AssertionError('terminal maintenance must not issue per-ID delete statements')
        def __getattr__(self, name): return getattr(self.connection, name)

    @contextmanager
    def connect():
        with db.connect() as c:
            yield Observed(c)

    queue = ExecutionQueue.__new__(ExecutionQueue)
    queue.changes = SimpleNamespace(connect=connect, forget_transient_events=lambda ids: forgotten.extend(ids),
                                    notify=lambda *kinds: notifications.append(kinds))
    yield db, queue, statements, forgotten, notifications
    with db.connect() as c: c.execute(f'DROP SCHEMA "{schema}" CASCADE')
    db.close()


def prune(queue):
    stamp = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
    return queue.prune_terminal(older_than_epoch=stamp('2026-11-10'),
                               unacknowledged_older_than_epoch=stamp('2026-11-01'))


@pytest.mark.parametrize('count', [0, 1, 100])
def test_terminal_batch_retains_boundaries_and_unrelated_metadata(pruning, count):
    """维护只删除已锁定且达到保留期的终态，元数据和级联同事务清理。"""
    db, queue, statements, forgotten, notifications = pruning
    ids = [f'old-{n}' for n in range(count)]
    protected = [('running', 'running', None, '2020-01-01'),
                 ('queued', 'queued', None, '2020-01-01'),
                 ('fresh', 'completed', '2026-11-11', '2026-11-11'),
                 ('unack', 'failed', None, '2026-11-05'),
                 ('boundary', 'cancelled', '2026-11-10', '2026-11-10')]
    with db.connect() as c:
        c.executemany('INSERT INTO executions VALUES(%s,%s,%s,%s)',
            [(identity, ['completed','failed','cancelled'][n % 3], '2020-01-01' if n % 2 else None, '2020-01-01')
             for n, identity in enumerate(ids)] + protected)
        all_ids = ids + [p[0] for p in protected]
        c.executemany('INSERT INTO execution_store_meta VALUES(%s,%s)',
            [(prefix + identity, 'value') for identity in all_ids for prefix in ['started:', 'request_digest:']] + [('other', 'value')])
        c.executemany('INSERT INTO child VALUES(%s)', [(identity,) for identity in all_ids])
    assert prune(queue) == count
    deletes = [sql for sql in statements if sql.startswith('DELETE')]
    assert len(deletes) == (2 if count else 0)
    assert set(forgotten) == set(ids)
    assert notifications == ([('execution',)] if count else [])
    with db.read() as c:
        assert {r['execution_id'] for r in c.execute('SELECT execution_id FROM executions').fetchall()} == {p[0] for p in protected}
        assert len(c.execute('SELECT * FROM child').fetchall()) == len(protected)
        remaining = {r['key'] for r in c.execute('SELECT key FROM execution_store_meta').fetchall()}
        assert remaining == {'other', *(prefix + p[0] for p in protected for prefix in ['started:', 'request_digest:'])}


def test_failed_metadata_delete_rolls_back_execution_delete(pruning):
    """元数据删除失败时执行行删除回滚，不能发布已清理通知。"""
    db, queue, _, forgotten, notifications = pruning
    with db.connect() as c:
        c.execute("INSERT INTO executions VALUES('old','completed',NULL,'2020-01-01')")
        c.execute("INSERT INTO execution_store_meta VALUES('started:old','value')")
        c.execute("CREATE FUNCTION refuse_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture refusal'; END $$")
        c.execute('CREATE TRIGGER refuse_delete BEFORE DELETE ON execution_store_meta FOR EACH ROW EXECUTE FUNCTION refuse_delete()')
    with pytest.raises(Exception, match='fixture refusal'):
        prune(queue)
    with db.read() as c:
        assert c.execute("SELECT execution_id FROM executions WHERE execution_id='old'").fetchone()
    assert forgotten == notifications == []

"""Installed cleanup opens cannot invert the path/installer lock order.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, local
from uuid import uuid4

import psycopg
import pytest

from management_common import directory_cleanup_store as cleanup
from management_common.directory_retention import RetentionStore, enqueue_directory_cleanup
from management_common.file_retention import remove_generated
from management_common.postgres_database import PostgresConnection


def test_generated_cleanup_and_path_owner_do_not_invert_locks(tmp_path, monkeypatch, record_property):
    """Real generated-file cleanup races an owner already holding paths.

    Observation only: the wrapper neither skips nor substitutes any SQL/lock.
    The other historical backend's identity is unknown; this owner is an
    isolated reproduction of the independently supported paths-first caller.
    """
    queue = cleanup.get_directory_cleanup_store()
    RetentionStore()
    root = tmp_path / 'generated'
    other = tmp_path / 'owner-tree'
    root.mkdir()
    other.mkdir()
    (root / 'data').write_text('runtime')
    paths_held, tail_requesting = Event(), Event()
    role = local()
    observations = []
    execute = PostgresConnection.execute

    def observed(connection, statement, parameters=None, **kwargs):
        name = getattr(role, 'name', '')
        if name and 'pg_advisory_xact_lock' in statement:
            pid = execute(connection, 'SELECT pg_backend_pid()').fetchone()[0]
            observations.append({'role': name, 'pid': pid, 'key': parameters[0]})
            if name == 'cleanup-tail' and parameters == (queue.table + ':paths',):
                tail_requesting.set()
        return execute(connection, statement, parameters, **kwargs)

    monkeypatch.setattr(PostgresConnection, 'execute', observed)
    with queue.database.read() as connection:
        keys = {kind: connection.execute('SELECT hashtextextended(%s,0)',
                    ('"directory_lifecycle".cleanup:' + kind,)).fetchone()[0]
                for kind in ('init', 'paths')}
    record_property('historical_unprefixed_key_pairs', json.dumps({
        k: [(v >> 32) & 0xffffffff, v & 0xffffffff] for k, v in keys.items()}))

    def path_owner():
        role.name = 'path-owner'
        with queue.database.transaction() as connection:
            queue.lock(connection)
            paths_held.set()
            assert tail_requesting.wait(5), 'cleanup did not request paths'
            return enqueue_directory_cleanup(other, owner_id='owner',
                operation_id='owner-end', actor='alice', connection=connection)

    def cleanup_tail():
        role.name = 'cleanup-tail'
        assert paths_held.wait(5), 'owner did not acquire paths'
        return remove_generated(root, owner_id='test-run')

    errors, results = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(path_owner), pool.submit(cleanup_tail)]
        for job in jobs:
            try:
                results.append(job.result(timeout=15))
            except Exception as exc:
                errors.append(f'{type(exc).__name__}: {exc}')
    record_property('lock_requests', json.dumps(observations))
    assert not errors, errors
    assert results[1]['status'] == 'completed'
    assert not root.exists() and not other.exists()
    assert all(row['status'] == 'completed' for row in queue.status())
    with queue.database.read() as connection:
        assert not connection.execute(f'SELECT 1 FROM {queue.reservations}').fetchall()


def test_installed_open_does_not_hold_initializer_lock():
    queue = cleanup.DirectoryCleanupStore()
    with queue.database.transaction() as connection:
        cleanup.DirectoryCleanupStore(connection=connection)
        with psycopg.connect(queue.database.dsn) as independent:
            assert independent.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                       (queue.table + ':init',)).fetchone()[0]


def test_partial_cleanup_installation_remains_rejected():
    queue = cleanup.DirectoryCleanupStore()
    with pytest.raises(RuntimeError, match='offline maintenance'):
        with queue.database.transaction() as connection:
            connection.execute(f'DROP TABLE {queue.reservations}')
            cleanup.DirectoryCleanupStore(connection=connection)
    with queue.database.read() as connection:
        assert connection.execute('SELECT to_regclass(%s)', (queue.reservations,)).fetchone()[0]


def test_concurrent_fresh_initializers_recheck_under_lock():
    queue = cleanup.DirectoryCleanupStore()
    schema = 'cleanup_cold_' + uuid4().hex
    from threading import Barrier
    barrier = Barrier(2)

    def install():
        with psycopg.connect(queue.database.dsn) as connection:
            barrier.wait(timeout=5)
            cleanup.initialize_directory_schema(connection, schema)
            cleanup.initialize_directory_schema(connection, schema)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(install) for _ in range(2)]
            for job in jobs:
                job.result(timeout=15)
        with queue.database.read() as connection:
            row = connection.execute('SELECT to_regclass(%s),to_regclass(%s)',
                (f'"{schema}".cleanup', f'"{schema}".path_reservations')).fetchone()
            assert row[0] and row[1]
    finally:
        with queue.database.transaction() as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

"""Real PostgreSQL admission/write barrier tests, only on pytest's isolated DB."""
import os
from types import SimpleNamespace
import psycopg
import pytest
import uuid
from psycopg import sql
from psycopg.conninfo import make_conninfo
from scripts import tfi_application_freeze as barrier


@pytest.fixture
def database():
    server = os.environ['PORTAL_POSTGRES_TEST_DSN']
    name = 'freeze_' + uuid.uuid4().hex
    with psycopg.connect(server, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(server, dbname=name)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute('CREATE TABLE public.application_freeze_fixture(value text)')
    yield dsn
    with psycopg.connect(server, autocommit=True) as connection:
        connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def test_write_barrier_retains_reads_and_releases_without_changing_intake(database):
    api = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': database})
    with barrier.freeze(api, [{'target': {}}]) as held:
        with psycopg.connect(database, autocommit=True) as writer:
            writer.execute("SET lock_timeout = '100ms'")
            assert writer.execute('SELECT count(*) FROM public.application_freeze_fixture').fetchone() == (0,)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                writer.execute("INSERT INTO public.application_freeze_fixture VALUES ('not acknowledged')")
            with pytest.raises(RuntimeError, match='other client'):
                barrier.assert_no_clients(held[0])
        # pg_stat_activity stats can cache within a transaction; explicitly
        # refresh on every offline check, including after diagnostics close.
        barrier.assert_no_clients(held[0])
    with psycopg.connect(database) as writer:
        writer.execute("INSERT INTO public.application_freeze_fixture VALUES ('accepted after release')")
        assert writer.execute('SELECT count(*) FROM public.application_freeze_fixture').fetchone() == (1,)


def test_existing_uncommitted_writer_refuses_freeze(database):
    api = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': database})
    with psycopg.connect(database) as writer:
        writer.execute("INSERT INTO public.application_freeze_fixture VALUES ('in flight')")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with barrier.freeze(api, [{'target': {}}]):
                pytest.fail('An uncommitted writer must prevent the offline barrier')


def test_other_database_client_blocks_cluster_shutdown_without_cancellation(database):
    from scripts import tfi_application_clients as clients
    api = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': database})
    with barrier.freeze(api, [{'target': {}}]) as held:
        with psycopg.connect(os.environ['PORTAL_POSTGRES_TEST_DSN'], autocommit=True) as other:
            with pytest.raises(RuntimeError, match='Other database'):
                clients.plan_disconnected(held[0], None, None)
            with pytest.raises(RuntimeError, match='other client'):
                barrier.assert_no_clients(held[0])
            assert other.execute('SELECT 1').fetchone() == (1,)
        barrier.assert_no_clients(held[0])


def test_guarded_cancel_wakes_only_blocked_sql_and_zero_clients_still_required(database):
    # Real PG signal/lock semantics; process ownership is exercised separately
    # by the Windows adapter/native acceptance, never inferred from this fake.
    from concurrent.futures import ThreadPoolExecutor
    import time
    from scripts import tfi_application_clients as clients
    api = SimpleNamespace(clean_environment=lambda _: {'PORTAL_DATABASE_URL': database})
    with barrier.freeze(api, [{'target': {}}]) as held:
        connection = held[0]
        blocker = connection.execute('SELECT pg_backend_pid()').fetchone()[0]
        with psycopg.connect(database, autocommit=True) as writer:
            pid = writer.info.backend_pid
            with ThreadPoolExecutor() as pool:
                future = pool.submit(writer.execute,
                    "INSERT INTO public.application_freeze_fixture VALUES ('must never commit')")
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        row = next(r for r in clients.clients(connection) if r['pid'] == pid)
                        if row['blockers'] == [blocker]:
                            break
                        assert time.monotonic() < deadline
                        time.sleep(.02)
                    evidence = {'barrier_pid': blocker, 'clients': {pid: {
                        'identity': {k: row[k] for k in clients.IDENTITY},
                        'endpoint': ('127.0.0.1', row['client_port'], '127.0.0.1', 5432),
                        'chain': [(999999, 1)]}}}
                    processes = SimpleNamespace(exited=lambda value: value != pid, tcp=lambda: [])
                    assert clients.clear_disconnected(connection, processes, evidence) == 1
                    with pytest.raises(psycopg.errors.QueryCanceled):
                        future.result(timeout=5)
                    assert connection.execute('SELECT count(*) FROM public.application_freeze_fixture').fetchone() == (0,)
                    with pytest.raises(RuntimeError, match='other client'):
                        barrier.assert_no_clients(connection)
                finally:
                    if not future.done():
                        writer.cancel()
        barrier.assert_no_clients(connection)

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

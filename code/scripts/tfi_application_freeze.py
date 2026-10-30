"""Offline application write barrier for old and new installed runtimes.

Share locks block writes without changing persisted intake or queue state.
Keep the connections open until the owned PostgreSQL servers have stopped.
No lock timeout, unknown writer or incomplete shutdown authorizes a snapshot.
"""
from contextlib import contextmanager, ExitStack
import time


def tables(connection):
    return connection.execute("""SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%'
        AND n.nspname<>'information_schema' ORDER BY n.nspname,c.relname""").fetchall()


@contextmanager
def freeze(api, records):
    import psycopg
    from psycopg import sql
    with ExitStack() as stack:
        connections = []
        for state in records:
            connection = psycopg.connect(api.clean_environment(state['target'])['PORTAL_DATABASE_URL'])
            # close() rolls back our lock-only transaction without issuing a
            # command to a server which may already have shut down cleanly.
            stack.callback(connection.close)
            connection.execute("SET LOCAL lock_timeout = '3s'")
            names = tables(connection)
            if not names:
                raise ValueError('No application tables to freeze')
            connection.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(
                sql.SQL(',').join(sql.Identifier(*name) for name in names)))
            if tables(connection) != names:
                raise ValueError('Application tables changed while acquiring write barrier')
            connections.append(connection)
        yield connections


def assert_no_clients(connection):
    # Every service has stopped. Unknown diagnostic sessions are not terminated
    # by maintenance: the operator must close them before taking the cold pair.
    deadline = time.monotonic() + 2
    while True:
        connection.execute('SELECT pg_stat_clear_snapshot()')
        count = connection.execute("""SELECT count(*) FROM pg_stat_activity
            WHERE datname=current_database() AND pid<>pg_backend_pid()
            AND backend_type='client backend'""").fetchone()[0]
        if not count:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Database still has other client sessions; offline maintenance refused')
        time.sleep(.05)

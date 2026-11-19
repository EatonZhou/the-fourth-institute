"""One-time certification of identity directory dependencies.

Run without --apply for schema readiness only. Stop services and other database
clients before --apply, or explicitly use --coordinated for a fail-fast,
writer-fenced cutover with a 30-second total transaction budget.
The earlier directory protocol migration is required.
No files are moved/deleted; an exception rolls back the complete rebuild.
"""
import argparse
import json
from pathlib import Path
import sys
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from management_common.postgres_database import PostgresConnection, _portal_row_factory
from management_common.portal_database import portal_database_url
from management_common import directory_dependencies
from scripts.instance_storage_migration import require_database_alone


def migrate(connection):
    require_database_alone(connection)
    from scripts.directory_dependency_migration import migrate_all_dependencies
    return migrate_all_dependencies(connection)


class _BudgetConnection(PostgresConnection):
    """Maintenance-only adapter; every owner SQL call shares one deadline."""

    def __init__(self, connection, check_budget):
        super().__init__(connection.raw)
        self.check_budget = check_budget

    def execute(self, statement, parameters=None, *, prepare=None):
        self.check_budget()
        result = super().execute(statement, parameters, prepare=prepare)
        self.check_budget()
        return result

    def executemany(self, statement, parameters):
        for row in parameters:
            self.execute(statement, row)

    def executescript(self, script):
        # The migration owners emit individual DDL statements; no arbitrary
        # SQL scripts or unbounded client batches enter this maintenance path.
        self.execute(script, prepare=False)


def coordinated_upgrade(connection):
    """One bounded cutover, with old structural writers fenced after commit.

    No sessions are terminated. A busy source makes this whole transaction
    fail immediately, leaving the installation unchanged for a later retry.
    """
    from management_common.portal_database import _database_schema
    from scripts.directory_dependency_migration import migrate_all_dependencies
    started = monotonic()
    control = connection
    def check_budget():
        remaining = 30.0 - (monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('Coordinated directory migration transaction budget exceeded; rollback required')
        # A row-by-row backfill must not renew a per-statement 60s allowance.
        # SQL at each stage is limited to the remaining whole-transaction time.
        control.execute("SELECT set_config('statement_timeout',%s,true)", (str(max(1, int(remaining * 1000))),))
    check_budget()
    connection = _BudgetConnection(connection, check_budget)
    connection.execute("SET LOCAL lock_timeout='250ms'")
    queue_table = '"' + _database_schema('directory_lifecycle') + '".cleanup'
    locked = connection.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                (queue_table + ':paths',)).fetchone()[0]
    if not locked:
        raise RuntimeError('Directory writer is busy; coordinated upgrade made no changes')
    # _claim_pending commits before filesystem removal. Its session lease,
    # not the persistent reservation, proves a worker still owns an old row.
    # The queue lock prevents any new claim while these nonblocking fences
    # are acquired and the preservation manifests are replaced.
    if connection.execute('SELECT to_regclass(%s)', (queue_table,)).fetchone()[0]:
        from management_common.directory_cleanup_store import DirectoryCleanupStore
        queue = DirectoryCleanupStore(connection=connection, create=False)
        pending = connection.execute(f'SELECT cleanup_id FROM {queue_table} WHERE completed_at IS NULL ORDER BY cleanup_id').fetchall()
        for row in pending:
            key = queue.worker_lease_key(row['cleanup_id'])
            if not connection.execute('SELECT pg_try_advisory_xact_lock(%s)', (key,)).fetchone()[0]:
                raise RuntimeError('Active cleanup worker; coordinated upgrade made no changes: ' + row['cleanup_id'])
    for area, names in (
        ('lineage', ['residents', 'lineages', 'lineage_nodes']),
        ('workspace', ['workspaces']), ('resource_registry', ['registry_documents']),
        ('directory_retention', ['artifacts']),
        ('directory_lifecycle', ['cleanup', 'path_reservations', 'moves']),
    ):
        for name in names:
            check_budget()
            table = '"' + _database_schema(area) + '".' + name
            if connection.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0]:
                mode = 'ACCESS EXCLUSIVE' if (area, name) == ('directory_lifecycle', 'cleanup') else 'SHARE ROW EXCLUSIVE'
                connection.execute(f'LOCK TABLE {table} IN {mode} MODE NOWAIT')
    result = migrate_all_dependencies(connection, check_budget=check_budget)
    check_budget()
    return {**result, 'transaction_budget_seconds': 30, 'elapsed_seconds': monotonic() - started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--coordinated', action='store_true', help='Use the explicit writer-fenced cutover; no service termination')
    args = parser.parse_args()
    with psycopg.connect(portal_database_url(), row_factory=_portal_row_factory) as raw:
        connection = PostgresConnection(raw)
        if args.apply:
            result = coordinated_upgrade(connection) if args.coordinated else migrate(connection)
        else:
            connection.execute('SET TRANSACTION READ ONLY')
            result = {'installed': directory_dependencies.installed(connection)}
            try:
                directory_dependencies.require_ready(connection)
                result['ready'] = True
            except directory_dependencies.DirectoryDependenciesMissing as error:
                result.update(ready=False, reason=str(error))
    print(json.dumps({'applied': args.apply, **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()

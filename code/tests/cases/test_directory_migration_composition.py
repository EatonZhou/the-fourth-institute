"""Installation composes owner backfills without changing their transaction."""
import pytest


@pytest.mark.parametrize('arguments', [{}, {'identities': [], 'identity_source': lambda c: None}])
def test_resource_migration_requires_one_explicit_identity_source(arguments):
    from management_portal.resources.directory_identity_lifecycle import migrate_dependencies

    class NoSQL:
        def execute(self, *args, **kwargs):
            raise AssertionError('Missing or ambiguous source must fail before SQL')

    with pytest.raises(ValueError, match='identity source'):
        migrate_dependencies(NoSQL(), **arguments)


@pytest.mark.parametrize('fail_source', [False, True])
def test_identity_source_and_index_share_transaction_and_rollback(tmp_path, fail_source):
    from management_common import directory_dependencies as deps
    from management_common.portal_database import portal_database
    from management_portal.resources.directory_identity_lifecycle import (
        migrate_dependencies, record_identity_directory,
    )
    database = portal_database(schema='directory_lifecycle')
    marker = '"' + database.schema + '".migration_source_probe'
    root = tmp_path / 'identity'
    root.mkdir()
    with database.transaction() as connection:
        migrate_dependencies(connection, identities=[])
        connection.execute(f'CREATE TABLE IF NOT EXISTS {marker}(identity text PRIMARY KEY)')
        before = [dict(row) for row in connection.execute(
            f'SELECT * FROM {deps.schema()}.directory_dependencies ORDER BY kind,object_id,slot',
        ).fetchall()]

    seen = []
    def source(connection):
        assert connection is active
        assert connection.execute('SELECT pg_current_xact_id()::text').fetchone()[0] == xid
        assert not connection.execute(f'SELECT ready FROM {deps.schema()}.directory_dependency_state').fetchone()[0]
        connection.execute(f'INSERT INTO {marker} VALUES (%s)', ('resident_000000009811',))
        record_identity_directory('resident_000000009811', root, connection=connection)
        seen.append(True)
        if fail_source:
            raise RuntimeError('authoritative source rejected')

    def run():
        nonlocal active, xid
        with database.transaction() as active:
            xid = active.execute('SELECT pg_current_xact_id()::text').fetchone()[0]
            migrate_dependencies(active, identity_source=source)

    active = xid = None
    if fail_source:
        with pytest.raises(RuntimeError, match='authoritative source rejected'):
            run()
    else:
        run()
    assert seen == [True]
    with database.read() as connection:
        deps.require_ready(connection)
        assert connection.execute(f'SELECT count(*) FROM {marker}').fetchone()[0] == (0 if fail_source else 1)
        after = [dict(row) for row in connection.execute(
            f'SELECT * FROM {deps.schema()}.directory_dependencies ORDER BY kind,object_id,slot',
        ).fetchall()]
        if fail_source:
            assert after == before
        else:
            assert any(row['object_id'] == 'resident_000000009811' for row in after)


@pytest.mark.parametrize('entry', ['installer', 'coordinated'])
def test_official_entrypoints_compose_society_source_in_their_transaction(monkeypatch, entry):
    from management_common import directory_dependencies as deps
    from management_common.portal_database import portal_database
    from management_portal.society_service import directory_owner
    from scripts.migrate_identity_directory_dependencies import coordinated_upgrade
    from scripts.tfi_release_initialization import initialize_directory_dependencies
    database = portal_database(schema='directory_lifecycle')
    seen = []

    def source(connection):
        assert connection.raw is active.raw
        assert connection.execute('SELECT pg_current_xact_id()::text').fetchone()[0] == xid
        assert not connection.execute(f'SELECT ready FROM {deps.schema()}.directory_dependency_state').fetchone()[0]
        seen.append(True)
        return {'lineages': 0, 'residents': 0}

    monkeypatch.setattr(directory_owner, 'migrate_identity_directories', source)
    with database.transaction() as active:
        xid = active.execute('SELECT pg_current_xact_id()::text').fetchone()[0]
        if entry == 'installer':
            initialize_directory_dependencies(active.raw)
        else:
            coordinated_upgrade(active)
        deps.require_ready(active)
    assert seen == [True]

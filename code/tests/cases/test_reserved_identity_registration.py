"""Real PostgreSQL checks for first identity publication under durable claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import pytest
import psycopg

from management_common import directory_dependencies as deps
from management_common.directory_cleanup_store import DirectoryPendingCleanup
from management_common.directory_paths import cleanup_location
from management_common.portal_database import _database_schema
from management_common.postgres_database import PostgresConnection, _portal_row_factory
from management_portal.resources.creation import Creation
from management_portal.resources import directory_identity_lifecycle as identities
from tests.cases.test_resident_folder_creation_contract import future_creation_store


@contextmanager
def published(tmp_path):
    identity = 'resident_' + uuid4().hex[:12]
    root = tmp_path / 'target'
    creation = Creation(identity, actor='test', payload={})
    with creation.prepare([(root, 'directory')]):
        creation.publish(lambda _, stage: (stage / 'payload').write_text('owned'))
        yield creation, identity, root


def projection(connection, identity):
    return connection.execute(f'SELECT * FROM {deps.schema()}.directory_dependencies '
                              "WHERE kind='identity' AND object_id=%s", (identity,)).fetchall()


def assert_rolled_back(creation, identity):
    with creation.db.read() as connection:
        assert not projection(connection, identity)
        assert connection.execute(f'SELECT 1 FROM {creation.queue.reservations} '
                                  'WHERE operation_id=%s', (creation.key,)).fetchone()
        assert not connection.execute(f'SELECT completed FROM {creation.table} '
                                      'WHERE operation_id=%s', (creation.key,)).fetchone()[0]


def test_initial_index_and_completion_share_owner_transaction(future_creation_store, tmp_path):
    with published(tmp_path) as (creation, identity, root):
        with creation.db.transaction() as connection, creation.identity_registration(connection):
            identities.record_identity_directory(identity, root, connection=connection)
            assert len(projection(connection, identity)) == 1
            with creation.registration(connection):
                pass
        with creation.db.read() as connection:
            assert len(projection(connection, identity)) == 1
            assert not connection.execute(f'SELECT 1 FROM {creation.queue.reservations} '
                                          'WHERE operation_id=%s', (creation.key,)).fetchone()


@pytest.mark.parametrize('ancestor', [False, True])
def test_cleanup_cannot_claim_then_final_conflict_rolls_back(future_creation_store, tmp_path, ancestor):
    with published(tmp_path) as (creation, identity, root):
        def enqueue_and_claim():
            with creation.db.transaction() as other:
                cleanup = creation.queue.enqueue(root.parent if ancestor else root,
                    owner_id='other', operation_id='concurrent-cleanup', actor='test', connection=other)
            # Real worker admission reads the durable reservation, not this TX's index.
            with creation.queue._claim_pending(cleanup) as selected:
                assert selected is None
            return cleanup

        with pytest.raises(DirectoryPendingCleanup):
            with creation.db.transaction() as connection, creation.identity_registration(connection):
                identities.record_identity_directory(identity, root, connection=connection)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    cleanup = pool.submit(enqueue_and_claim).result(timeout=3)
                assert cleanup and (root / 'payload').read_text() == 'owned'
                with creation.registration(connection):
                    pass
        assert_rolled_back(creation, identity)
        assert (root / 'payload').read_text() == 'owned'


@pytest.mark.parametrize('fault', ['forged_receipt', 'closed_session', 'expired_context'])
def test_invalid_writer_or_expired_protection_rejected(future_creation_store, tmp_path, fault):
    with published(tmp_path) as (creation, identity, root):
        original_token = creation.writer.receipt['token']
        try:
            with pytest.raises(RuntimeError):
                with creation.db.transaction() as connection:
                    if fault == 'forged_receipt':
                        creation.writer.receipt['token'] = 'forged'
                    elif fault == 'closed_session':
                        creation.writer.session.close()
                    with creation.identity_registration(connection):
                        proof = identities._INITIAL_REGISTRATION.get()
                        if fault == 'expired_context':
                            proof.active = False
                        identities.record_identity_directory(identity, root, connection=connection)
        finally:
            creation.writer.receipt['token'] = original_token
        assert_rolled_back(creation, identity)


@pytest.mark.parametrize('fault', ['missing', 'wrong_purpose', 'uncommitted'])
def test_requires_preexisting_durable_reservation(future_creation_store, tmp_path, fault):
    with published(tmp_path) as (creation, identity, root):
        with pytest.raises(RuntimeError, match='durable creation reservations'):
            with creation.db.transaction() as connection:
                deps.prepare_write(connection)
                connection.execute(f'DELETE FROM {creation.queue.reservations} WHERE operation_id=%s', (creation.key,))
                if fault != 'missing':
                    connection.execute(f'INSERT INTO {creation.queue.reservations}(operation_id,location,purpose) '
                        'VALUES (%s,%s,%s)', (creation.key, cleanup_location(root),
                                            'other' if fault == 'wrong_purpose' else 'resource_creation'))
                    creation.queue.refresh_reservations(creation.key, connection=connection)
                with creation.identity_registration(connection):
                    pytest.fail('Unprotected owner writes must not start')
        assert_rolled_back(creation, identity)


@pytest.mark.parametrize('fault', ['previous', 'retired', 'uncovered'])
def test_only_first_covered_identity_allowed(future_creation_store, tmp_path, fault):
    with published(tmp_path) as (creation, identity, root):
        with creation.db.transaction() as connection:
            if fault == 'previous':
                deps.replace(connection, 'identity', identity, [('', identity, root, 'owned')])
            elif fault == 'retired':
                connection.execute(f'INSERT INTO {deps.schema()}.identity_retirements VALUES (%s,%s,%s::jsonb)',
                                   (identity, cleanup_location(root), '{}'))
        with pytest.raises(ValueError):
            with creation.db.transaction() as connection, creation.identity_registration(connection):
                identities.record_identity_directory(identity, root.parent if fault == 'uncovered' else root,
                                                     connection=connection)
        with creation.db.read() as connection:
            assert len(projection(connection, identity)) == (1 if fault == 'previous' else 0)


@pytest.mark.parametrize('fault', ['not_ready', 'dirty', 'missing_index', 'missing_claim_table'])
def test_uninstalled_or_incomplete_index_rejected_before_owner_sql(future_creation_store, tmp_path, fault):
    with published(tmp_path) as (creation, identity, root):
        with pytest.raises(deps.DirectoryDependenciesMissing):
            with creation.db.transaction() as connection:
                if fault == 'not_ready':
                    connection.execute(f'UPDATE {deps.schema()}.directory_dependency_state SET ready=false')
                elif fault == 'dirty':
                    connection.execute(f'INSERT INTO {deps.schema()}.directory_dependency_dirty VALUES (%s,%s)',
                                       ('resource', 'unrefreshed'))
                elif fault == 'missing_index':
                    connection.execute(f'DROP TABLE {deps.schema()}.directory_dependencies')
                else:
                    connection.execute(f'DROP TABLE {creation.queue.reservations}')
                with creation.identity_registration(connection):
                    pytest.fail('Owner SQL cannot start with incomplete migration')
        assert_rolled_back(creation, identity)


def test_rollback_after_final_release_retains_claim_and_no_half_index(future_creation_store, tmp_path):
    with published(tmp_path) as (creation, identity, root):
        with pytest.raises(RuntimeError, match='owner failed'):
            with creation.db.transaction() as connection, creation.identity_registration(connection):
                identities.record_identity_directory(identity, root, connection=connection)
                with creation.registration(connection):
                    raise RuntimeError('owner failed after reservation release')
        assert_rolled_back(creation, identity)


def test_protection_cannot_cross_connection_or_transaction(future_creation_store, tmp_path):
    with published(tmp_path) as (creation, identity, root):
        with psycopg.connect(creation.db.dsn, autocommit=True, row_factory=_portal_row_factory) as raw:
            connection = PostgresConnection(raw)
            connection.execute('BEGIN')
            with pytest.raises(RuntimeError, match='another transaction'), creation.identity_registration(connection):
                proof = identities._INITIAL_REGISTRATION.get()
                def other_connection():
                    with creation.db.transaction() as other:
                        with pytest.raises(RuntimeError, match='another transaction'):
                            deps.replace_initial_identity(other, identity, root, protection=proof)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(other_connection).result(timeout=3)
                # Same wrapper, new transaction must not reuse a previous attestation.
                connection.raw.rollback()
                connection.execute('SELECT 1')
                connection.execute('BEGIN')
                identities.record_identity_directory(identity, root, connection=connection)
            connection.raw.rollback()
        assert_rolled_back(creation, identity)


def test_context_does_not_grant_legacy_structural_writer_version(future_creation_store, tmp_path):
    with published(tmp_path) as (creation, identity, root):
        with pytest.raises(psycopg.errors.RaiseException, match='Directory dependency writer upgrade required'):
            with creation.db.transaction() as connection, creation.identity_registration(connection):
                assert connection.execute("SELECT current_setting('tfi.directory_dependency_writer',true)").fetchone()[0] != 'v1'
                connection.execute(f'INSERT INTO "{_database_schema("lineage")}".lineages(lineage_id) VALUES (%s)',
                                   ('lineage_0000000faded1',))
        assert_rolled_back(creation, identity)


def test_descendant_identity_does_not_follow_symlink(future_creation_store, tmp_path):
    with published(tmp_path) as (creation, identity, root):
        (root / 'real').mkdir()
        (root / 'alias').symlink_to(root / 'real', target_is_directory=True)
        with pytest.raises(FileExistsError, match='symbolic link'):
            with creation.db.transaction() as connection, creation.identity_registration(connection):
                identities.record_identity_directory(identity, root / 'alias', connection=connection)
        assert_rolled_back(creation, identity)


@pytest.mark.parametrize('field', ['completed', 'aborted'])
def test_completed_or_cancelled_journal_rejects_context(future_creation_store, tmp_path, field):
    with published(tmp_path) as (creation, identity, root):
        with pytest.raises(RuntimeError):
            with creation.db.transaction() as connection:
                connection.execute(f'UPDATE {creation.table} SET {field}=true WHERE operation_id=%s', (creation.key,))
                with creation.identity_registration(connection):
                    pytest.fail('Ended creation must not start identity writes')
        assert_rolled_back(creation, identity)

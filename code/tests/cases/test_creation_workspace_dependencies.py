"""Creation owns complete immutable declarations and target/staging correlation."""
import json

import pytest

from management_portal.resources.creation import (
    Creation, CreationDependencyUnknown, creation_dependency_record,
    creation_operation_key, initialize_creation_schema, pending_creation_workspace_dependency,
)
from tests.cases.test_workspace_redirected_retirement import workspace, resource_workspace, prepare, call
from tests.cases.test_workspace_resource_rename import WID

CHILD = 'workspace_111111111111'
RID = 'resident_abc012345678'


def operation(dependencies=None):
    return Creation(RID, actor='alice', payload={'frozen': True}, operation_id='dependency-case',
                    workspace_dependencies=dependencies)


def test_declaration_precedes_claims_and_staging_has_same_owner(workspace, monkeypatch):
    prepare(workspace)
    creation = operation([WID, WID])
    target = workspace.root / 'new-child'
    original = creation.queue.reserve
    claims = []
    def reserve(path, *, operation_id, purpose, connection):
        row = creation_dependency_record(creation.key, connection=connection)
        assert row['workspace_dependencies'] == {'version': 1, 'workspace_ids': [WID]}
        claims.append(operation_id)
        return original(path, operation_id=operation_id, purpose=purpose, connection=connection)
    monkeypatch.setattr(creation.queue, 'reserve', reserve)
    with creation.prepare([(target, 'directory')]):
        creation.publish(lambda _index, path: (path / 'payload').write_text('new contents'))
        with creation.db.transaction() as connection:
            row = creation_dependency_record(creation.key, connection=connection)
            stage = next(key for key in row['claim_operation_ids'] if key != creation.key)
            assert creation_dependency_record(stage, connection=connection) == row
            assert row['payload'] == {'frozen': True}
            creation.assert_workspace_dependencies([WID], connection=connection)
        assert creation.key == creation_operation_key('alice', 'dependency-case')
    assert len(claims) >= 2


@pytest.mark.parametrize('original,next_value', [(None, []), ([], [WID]), ([WID], [])])
def test_unfinished_retry_cannot_backfill_or_change_declaration(workspace, original, next_value):
    prepare(workspace)
    creation = operation(original)
    with creation.prepare([(workspace.root / 'new', 'directory')]):
        pass
    with creation.db.read() as connection:
        before = dict(connection.execute(f'SELECT * FROM {creation.table}').fetchone())
    with pytest.raises(CreationDependencyUnknown, match='declaration changed'):
        with operation(next_value).prepare([(workspace.root / 'new', 'directory')]):
            pytest.fail('changed declaration admitted')
    with creation.db.read() as connection:
        after = dict(connection.execute(f'SELECT * FROM {creation.table}').fetchone())
    assert after == before


def test_same_declaration_retry_and_actual_subset(workspace):
    prepare(workspace)
    with operation([WID]).prepare([(workspace.root / 'new', 'directory')]):
        pass
    creation = operation([WID, WID])
    with creation.prepare([(workspace.root / 'new', 'directory')]):
        with creation.db.transaction() as connection:
            creation.assert_workspace_dependencies([WID], connection=connection)
            with pytest.raises(ValueError, match='exceed declared'):
                creation.assert_workspace_dependencies([WID, CHILD], connection=connection)


def test_retired_dependency_rejected_before_claim_or_files(workspace):
    prepare(workspace)
    call(workspace).raise_for_error()
    creation = operation([WID])
    target = workspace.root / 'new'
    with pytest.raises(ValueError, match='dependency was retired'):
        with creation.prepare([(target, 'directory')]):
            pytest.fail('retired dependency admitted')
    assert not target.exists()
    with creation.db.read() as connection:
        assert creation_dependency_record(creation.key, connection=connection) is None
        assert not connection.execute(f'SELECT 1 FROM {creation.queue.reservations} '
                                      'WHERE operation_id=%s', (creation.key,)).fetchone()


def test_legacy_schema_reader_never_initializes_or_backfills(workspace):
    prepare(workspace)
    creation = operation()
    with creation.prepare([(workspace.root / 'legacy', 'directory')]):
        pass
    with creation.db.transaction() as connection:
        connection.execute(f'ALTER TABLE {creation.table} DROP COLUMN workspace_dependencies')
        with pytest.raises(CreationDependencyUnknown, match='schema is unavailable'):
            creation_dependency_record(creation.key, connection=connection)
        assert pending_creation_workspace_dependency(WID, connection=connection) == 'unknown'
        assert connection.execute('SELECT COUNT(*) FROM pg_attribute '
            'WHERE attrelid=%s::regclass AND attname=%s AND NOT attisdropped',
            (creation.table, 'workspace_dependencies')).fetchone()[0] == 0
        initialize_creation_schema(connection, creation.db.schema)
        row = creation_dependency_record(creation.key, connection=connection)
        assert row['workspace_dependencies'] is None
        assert row['completed'] is False


def test_empty_installed_legacy_schema_proves_no_pending_operations_without_ddl(workspace):
    prepare(workspace)
    creation = operation()
    with creation.db.transaction() as connection:
        connection.execute(f'ALTER TABLE {creation.table} DROP COLUMN workspace_dependencies')
        assert pending_creation_workspace_dependency(WID, connection=connection) == 'independent'
        assert connection.execute('SELECT COUNT(*) FROM pg_attribute '
            'WHERE attrelid=%s::regclass AND attname=%s AND NOT attisdropped',
            (creation.table, 'workspace_dependencies')).fetchone()[0] == 0


@pytest.mark.parametrize('document', [
    {'version': True, 'workspace_ids': []},
    {'version': 2, 'workspace_ids': []},
    {'version': 1, 'workspace_ids': 'workspace_111111111111'},
    {'version': 1, 'workspace_ids': [], 'complete': False},
])
def test_corrupt_declaration_is_unknown_not_empty(workspace, document):
    prepare(workspace)
    creation = operation([])
    with creation.prepare([(workspace.root / 'new', 'directory')]):
        pass
    with creation.db.transaction() as connection:
        connection.execute(f'UPDATE {creation.table} SET workspace_dependencies=%s::jsonb',
                           (json.dumps(document),))
        with pytest.raises(CreationDependencyUnknown, match='declaration is invalid'):
            creation_dependency_record(creation.key, connection=connection)


def test_registration_rechecks_immutable_declaration_in_same_transaction(workspace):
    prepare(workspace)
    creation = operation([WID])
    target = workspace.root / 'new'
    with creation.prepare([(target, 'directory')]):
        creation.publish(lambda _index, path: (path / 'payload').write_text('preserve'))
        with creation.db.transaction() as connection:
            connection.execute(f'UPDATE {creation.table} SET workspace_dependencies=%s::jsonb',
                               (json.dumps({'version': 1, 'workspace_ids': []}),))
        with pytest.raises(CreationDependencyUnknown, match='missing or changed'):
            with creation.db.transaction() as connection, creation.registration(connection):
                pytest.fail('changed declaration committed')
        with creation.db.read() as connection:
            assert connection.execute(f'SELECT 1 FROM {creation.queue.reservations} '
                                      'WHERE operation_id=%s', (creation.key,)).fetchone()
            assert not creation_dependency_record(creation.key, connection=connection)['completed']
    assert (target / 'payload').read_text() == 'preserve'


def test_workspace_materialize_forwards_dependency_without_changing_payload(workspace):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    prepare(workspace)
    row, _ = WorkspaceCreationStore().materialize(path=workspace.root / 'copy', actor='alice',
        operation_id='dependency-copy', contents={'source': 'frozen'},
        initialize=lambda stage, _effects: (stage / 'file.txt').write_text('copy'),
        workspace_dependencies=[WID])
    creation = operation()
    with creation.db.transaction() as connection:
        evidence = creation_dependency_record(creation_operation_key('alice', 'workspace:dependency-copy'),
                                              connection=connection)
    assert evidence['completed'] and evidence['resource_id'] == row['workspace_id']
    assert evidence['workspace_dependencies'] == {'version': 1, 'workspace_ids': [WID]}
    assert 'workspace_dependencies' not in evidence['payload']


def test_malformed_staging_correlation_is_unknown(workspace):
    prepare(workspace)
    creation = operation([])
    with creation.prepare([(workspace.root / 'new', 'directory')]):
        pass
    with creation.db.transaction() as connection:
        connection.execute(f"UPDATE {creation.table} SET artifacts=jsonb_set(artifacts, '{{0,stage_operation}}', '42')")
        with pytest.raises(CreationDependencyUnknown, match='artifact correlation'):
            creation_dependency_record(creation.key, connection=connection)


@pytest.mark.parametrize('cold_schema', [False, True])
def test_workspace_legacy_copy_recovers_original_null_without_backfill(workspace, cold_schema):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    from management_portal.society_service.workspace_dependencies import recoverable_workspace_dependencies
    prepare(workspace)
    store = WorkspaceCreationStore()
    arguments = dict(path=workspace.root / 'legacy-copy', actor='alice',
                     operation_id='legacy-copy', contents={'source': WID})
    def interrupted(stage, effects):
        raise RuntimeError('interrupted old copy')
    with pytest.raises(RuntimeError, match='interrupted old copy'):
        store.materialize(**arguments, initialize=interrupted)
    creation = operation()
    key = creation_operation_key('alice', 'workspace:legacy-copy')
    with creation.db.transaction() as connection:
        before = creation_dependency_record(key, connection=connection)
        assert before['workspace_dependencies'] is None
        if cold_schema:
            connection.execute(f'ALTER TABLE {creation.table} DROP COLUMN workspace_dependencies')
    row, _ = store.materialize(**arguments,
        initialize=lambda stage, effects: (stage / 'copied.txt').write_text('old request'),
        workspace_dependencies=[WID], resolve_workspace_dependencies=recoverable_workspace_dependencies)
    with creation.db.transaction() as connection:
        after = creation_dependency_record(key, connection=connection)
        assert after['completed'] and after['workspace_dependencies'] is None
        assert after['payload'] == before['payload'] and after['resource_id'] == row['workspace_id']
    assert (arguments['path'] / 'copied.txt').read_text() == 'old request'


def test_workspace_legacy_recovery_flag_cannot_change_original_payload(workspace):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    from management_portal.society_service.workspace_dependencies import recoverable_workspace_dependencies
    prepare(workspace)
    store = WorkspaceCreationStore()
    arguments = dict(path=workspace.root / 'legacy-copy', actor='alice', operation_id='legacy-copy')
    def interrupted(stage, effects):
        raise RuntimeError('interrupted old copy')
    with pytest.raises(RuntimeError, match='interrupted old copy'):
        store.materialize(**arguments, contents={'source': WID}, initialize=interrupted)
    with pytest.raises(ValueError, match='different arguments'):
        store.materialize(**arguments, contents={'source': CHILD}, initialize=interrupted,
            workspace_dependencies=[WID], resolve_workspace_dependencies=recoverable_workspace_dependencies)


def test_workspace_legacy_commit_rechecks_original_unknown_proof(workspace, monkeypatch):
    from contextlib import contextmanager
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    from management_portal.society_service.workspace_dependencies import recoverable_workspace_dependencies
    prepare(workspace)
    store = WorkspaceCreationStore()
    arguments = dict(path=workspace.root / 'legacy-copy', actor='alice',
                     operation_id='legacy-copy', contents={'source': WID})
    def interrupted(stage, effects):
        raise RuntimeError('interrupted old copy')
    with pytest.raises(RuntimeError, match='interrupted old copy'):
        store.materialize(**arguments, initialize=interrupted)
    original = Creation.registration
    @contextmanager
    def changed(creation, connection):
        connection.execute(f'UPDATE {creation.table} SET workspace_dependencies=%s::jsonb '
            'WHERE operation_id=%s', (json.dumps({'version': 1, 'workspace_ids': []}), creation.key))
        with original(creation, connection):
            yield
    monkeypatch.setattr(Creation, 'registration', changed)
    with pytest.raises(ValueError, match='Legacy Workspace creation proof changed'):
        store.materialize(**arguments, initialize=lambda stage, effects: None,
            workspace_dependencies=[WID], resolve_workspace_dependencies=recoverable_workspace_dependencies)
    creation = operation()
    with creation.db.transaction() as connection:
        row = creation_dependency_record(creation_operation_key('alice', 'workspace:legacy-copy'), connection=connection)
        assert row['workspace_dependencies'] is None and not row['completed']


@pytest.mark.parametrize('resolved', [None, []])
def test_resolver_cannot_erase_new_request_dependencies(workspace, resolved):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    prepare(workspace)
    target = workspace.root / 'new-copy'
    with pytest.raises(ValueError, match='Legacy Workspace creation recovery|changed the proposed dependencies'):
        WorkspaceCreationStore().materialize(path=target, actor='alice', operation_id='new-copy',
            contents={'source': WID}, initialize=lambda stage, effects: None,
            workspace_dependencies=[WID], resolve_workspace_dependencies=lambda *a, **kw: resolved)
    assert not target.exists()

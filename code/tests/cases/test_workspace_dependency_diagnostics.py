"""An authorized refusal describes missing proof without identifying private work."""
import json

import pytest

from management_portal.resources.creation import Creation, pending_creation_workspace_dependency
from tests.cases.test_workspace_redirected_retirement import workspace, resource_workspace, prepare, call
from tests.cases.test_workspace_resource_rename import WID


def test_unknown_diagnostic_is_bounded_and_preserves_private_operations(workspace, tmp_path, monkeypatch):
    prepare(workspace)
    operations = []
    for index in (1, 2):
        creation = Creation('resident_' + str(index) * 12, actor='private-actor-' + str(index),
                            payload={'private': str(index)}, operation_id='private-operation-' + str(index))
        with creation.prepare([(tmp_path / ('private-path-' + str(index)), 'directory')]):
            pass
        operations.append(creation)
    creation = operations[0]
    with creation.db.read() as connection:
        before = connection.execute(f'SELECT * FROM {creation.table} ORDER BY operation_id').fetchall()
    result = call(workspace)
    assert result.status == 'failed'
    assert result.error['code'] == 'instruction.invalid_arguments'
    assert result.error['commit_state'] == 'not_committed'
    assert result.error['details'] == {
        'reason': 'workspace_dependency_unknown',
        'dependency_diagnostics': [{'source': 'resource_creation', 'reason': 'missing_declaration'}],
    }
    assert 'private-' not in json.dumps(result.error)
    with creation.db.read() as connection:
        assert connection.execute(f'SELECT * FROM {creation.table} ORDER BY operation_id').fetchall() == before
    assert workspace.store.rows([WID])
    assert not workspace.store.cleanup.status(owner_id=WID)
    from tests.cases.permission_test_support import enable, Fact
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('workspace', WID))
    permissions.grant('bob', WID, '@contributor')
    denied = call(workspace, actor='bob')
    assert denied.status == 'failed' and denied.error['code'] == 'instruction.forbidden'
    assert 'dependency_diagnostics' not in denied.error['details']


@pytest.mark.parametrize('declaration,expected,reason', [
    ({'version': 999, 'workspace_ids': []}, 'unknown', 'invalid_declaration'),
    ({'version': 1, 'workspace_ids': [WID]}, 'dependent', 'declared_reference'),
])
def test_diagnostics_do_not_change_classification(workspace, tmp_path, declaration, expected, reason):
    prepare(workspace)
    creation = Creation('resident_111111111111', actor='alice', payload={}, operation_id='diagnostic')
    with creation.prepare([(tmp_path / 'candidate', 'directory')]):
        pass
    with creation.db.transaction() as connection:
        connection.execute(f'UPDATE {creation.table} SET workspace_dependencies=%s::jsonb',
                           (json.dumps(declaration),))
        diagnostics = []
        assert pending_creation_workspace_dependency(WID, connection=connection) == expected
        assert pending_creation_workspace_dependency(WID, connection=connection, diagnostics=diagnostics) == expected
        assert diagnostics == [{'source': 'resource_creation', 'reason': reason}]


def test_missing_legacy_column_is_reported_without_initialization(workspace, tmp_path):
    prepare(workspace)
    creation = Creation('resident_111111111111', actor='alice', payload={}, operation_id='legacy-diagnostic')
    with creation.prepare([(tmp_path / 'legacy', 'directory')]):
        pass
    with creation.db.transaction() as connection:
        connection.execute(f'ALTER TABLE {creation.table} DROP COLUMN workspace_dependencies')
        diagnostics = []
        assert pending_creation_workspace_dependency(WID, connection=connection, diagnostics=diagnostics) == 'unknown'
        assert diagnostics == [{'source': 'resource_creation', 'reason': 'legacy_schema_missing_declaration'}]
        assert not connection.execute('SELECT 1 FROM pg_attribute WHERE attrelid=%s::regclass '
                                      "AND attname='workspace_dependencies' AND NOT attisdropped", (creation.table,)).fetchone()


def test_owner_transport_preserves_refusal_details(workspace):
    from management_portal.hatg.contracts import InstructionError
    from management_portal.resources.errors import ResourceOperationError
    from management_portal.resources.directory_registrations import retire_workspace_registration
    from management_portal.society_service.transport import SocietyServiceClient

    prepare(workspace)
    diagnostics = [{'source': 'resource_creation', 'reason': 'missing_declaration'}]
    with workspace.store.database.transaction() as connection:
        with pytest.raises(ResourceOperationError) as caught:
            retire_workspace_registration(workspace.store.rows([WID])[WID], connection=connection,
                                           actor='alice', pending_diagnostics=diagnostics)
    with pytest.raises(InstructionError) as remote:
        SocietyServiceClient._raise_remote_error(caught.value.failure_payload())
    assert remote.value.code == 'instruction.invalid_arguments'
    assert remote.value.commit_state == 'not_committed'
    assert remote.value.details['dependency_diagnostics'] == diagnostics

"""Retire an isolated stale registration without deleting redirected content."""
import pytest

from management_common import directory_dependencies as deps
from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_workspace_unregistration import resource_workspace
from tests.cases.test_workspace_resource_rename import WID
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload,
)


@pytest.fixture
def workspace(resource_workspace):
    resource_workspace.root = resource_workspace.root / 'legacy-layout' / 'registered'
    resource_workspace.root.mkdir(parents=True)
    resource_workspace.store.ensure(WID, str(resource_workspace.root), move=True)
    return resource_workspace


def call(workspace, actor='alice', **options):
    return workspace.call('UnregisterResource', {'resource_id': WID, 'preserve_files': True, **options}, actor=actor)


def redirect(workspace):
    old = workspace.root.parent
    moved = old.with_name(old.name + '-shared')
    old.rename(moved)
    old.symlink_to(moved, target_is_directory=True)
    return old, moved, moved / workspace.root.name


def prepare(workspace):
    # Isolated installation only, never a production repair or read fallback.
    get_lineage_data_store()
    with workspace.store.database.transaction() as connection:
        migrate_dependencies(connection, identities=[])


def test_formal_retirement_preserves_link_target_and_records_no_cleanup(workspace):
    (workspace.root / 'shared.txt').write_text('keep every byte')
    prepare(workspace)
    link, target, directory = redirect(workspace)
    before = (link.lstat().st_ino, target.stat().st_ino)
    result = call(workspace).raise_for_error()
    assert result['attributes']['status'] == 'deleted'
    assert result['attributes']['directory_retirement']['reason'] == 'preserve_files'
    assert workspace.store.rows([WID]) == {}
    assert workspace.call('ResolveResource', {'address': WID}).status == 'failed'
    assert not workspace.store.cleanup.status(owner_id=WID)
    workspace.store.cleanup.run_pending()
    assert link.is_symlink() and (link.lstat().st_ino, target.stat().st_ino) == before
    assert (directory / 'shared.txt').read_text() == 'keep every byte'
    with workspace.store.database.read() as connection:
        plan = connection.execute(f'SELECT plan FROM {deps.schema()}.identity_retirements WHERE identity=%s', (WID,)).fetchone()['plan']
        assert plan['actor'] == 'alice' and plan['cleanup_ids'] == []
        assert plan['resource_ids'] == [WID]
        assert plan['binding_count'] == 0 and plan['nested_count'] == 0
    # Restoring the old parent cannot turn this retirement into future deletion.
    link.unlink()
    target.rename(link)
    workspace.store.cleanup.run_pending()
    assert (workspace.root / 'shared.txt').read_text() == 'keep every byte'


@pytest.mark.parametrize('placement', ['old', 'target'])
def test_nested_registration_prevents_retirement(workspace, placement):
    child = 'workspace_111111111111'
    if placement == 'old':
        workspace.store.ensure(child, str(workspace.root / 'child'))
    link, target, directory = redirect(workspace)
    if placement == 'target':
        workspace.store.ensure(child, str(directory / 'child'))
    prepare(workspace)
    result = call(workspace)
    assert result.status == 'failed' and result.error['commit_state'] == 'not_committed'
    assert 'nested' in result.error['message'].lower(), result.to_payload()
    assert set(workspace.store.rows([WID, child])) == {WID, child}
    assert not workspace.store.cleanup.status(owner_id=WID)
    assert link.is_symlink() and directory.is_dir()


def test_missing_dependencies_fails_closed(workspace):
    redirect(workspace)
    result = call(workspace)
    assert result.status == 'failed'
    assert workspace.store.rows([WID])
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_retirement_rolls_back_when_metadata_owner_fails(workspace, monkeypatch):
    from management_portal.resources import directory_registrations
    prepare(workspace)
    link, target, directory = redirect(workspace)
    def fail(*args, **kwargs):
        raise RuntimeError('metadata owner failure')
    monkeypatch.setattr(directory_registrations, '_unregister_identity_metadata', fail)
    result = call(workspace)
    assert result.status == 'failed'
    assert 'metadata owner failure' in result.error['message'], result.to_payload()
    assert workspace.store.rows([WID])
    with workspace.store.database.read() as connection:
        assert not connection.execute(f'SELECT 1 FROM {deps.schema()}.identity_retirements WHERE identity=%s', (WID,)).fetchone()
    assert link.is_symlink() and directory.is_dir()


def test_denied_actor_cannot_retire_redirected_registration(workspace, monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('workspace', WID))
    permissions.grant('bob', WID, '@contributor')
    prepare(workspace)
    redirect(workspace)
    result = call(workspace, actor='bob')
    assert result.status == 'failed' and result.error['code'] == 'instruction.forbidden'
    assert workspace.store.rows([WID])


def test_bound_workspace_is_not_retired(future_creation_store, workspace):
    from management_portal.society_service import resident_workspace_domain
    resident = resident_domain.register_resident(_new_session_payload('Still bound'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(workspace.root), 'sample_admin', workspace_id=WID)
    prepare(workspace)
    link, _, directory = redirect(workspace)
    result = call(workspace)
    assert result.status == 'failed' and 'bindings' in result.error['message']
    assert workspace.store.rows([WID])
    with get_lineage_data_store().database.read() as connection:
        assert connection.execute('SELECT 1 FROM resident_workspace_bindings WHERE workspace_id=%s', (WID,)).fetchone()
    assert link.is_symlink() and directory.is_dir()


def test_owner_artifact_cannot_be_silently_released(workspace):
    from management_common.directory_retention import RetentionStore
    retention = RetentionStore()
    artifact = workspace.root / 'artifact'
    artifact.mkdir()
    retention.declare(artifact, category='object_data', owner_id=WID)
    prepare(workspace)
    redirect(workspace)
    result = call(workspace)
    assert result.status == 'failed' and 'owned directories' in result.error['message']
    assert workspace.store.rows([WID])
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_foreign_owned_workspace_remains_registered(workspace):
    row = workspace.store.rows([WID])[WID]
    with workspace.store.database.transaction() as connection:
        workspace.store.restore_owner(WID, owner_id='lineage_111111111111',
            expected_owner=WID, expected_path=row['path'], expected_version=row['updated_at'], connection=connection)
    prepare(workspace)
    redirect(workspace)
    result = call(workspace)
    assert result.status == 'failed' and 'owned by another' in result.error['message']
    assert workspace.store.rows([WID])


def test_ordinary_unregister_never_falls_back_to_preservation(workspace):
    prepare(workspace)
    link, _, directory = redirect(workspace)
    result = call(workspace, preserve_files=False)
    assert result.status == 'failed' and 'symbolic link' in result.error['message']
    assert workspace.store.rows([WID]) and link.is_symlink() and directory.is_dir()
    with workspace.store.database.read() as connection:
        assert not connection.execute(f'SELECT 1 FROM {deps.schema()}.identity_retirements WHERE identity=%s', (WID,)).fetchone()


def test_explicit_preservation_also_keeps_a_canonical_directory(workspace):
    prepare(workspace)
    (workspace.root / 'keep.txt').write_text('kept')
    call(workspace).raise_for_error()
    assert (workspace.root / 'keep.txt').read_text() == 'kept'
    assert not workspace.store.rows([WID]) and not workspace.store.cleanup.status(owner_id=WID)


@pytest.mark.parametrize('value', ['true', 1, None])
def test_invalid_preservation_option_rejected(workspace, value):
    result = call(workspace, preserve_files=value)
    assert result.status == 'failed' and workspace.store.rows([WID])
    with pytest.raises(ValueError, match='boolean'):
        workspace.service.unregister(WID, actor='alice', preserve_files=value)


def test_pending_ancestor_claim_blocks_preservation(workspace):
    prepare(workspace)
    with workspace.store.database.transaction() as connection:
        workspace.store.cleanup.reserve(workspace.root.parent,
            operation_id='another-operation', purpose='resource_creation', connection=connection)
    result = call(workspace)
    assert result.status == 'failed' and 'claims' in result.error['message']
    assert workspace.store.rows([WID])


def test_unsupported_provider_cannot_fall_back_to_ordinary_deletion(workspace):
    from dataclasses import replace
    from unittest.mock import Mock
    workspace.service._ensure_mutation_providers('workspace')
    provider = workspace.service._mutation_providers.provider_for('workspace')
    ordinary = Mock(side_effect=AssertionError('must not delete files'))
    workspace.service._mutation_providers.register('workspace',
        replace(provider, unregister=ordinary, unregister_preserving_files=None), replace=True)
    result = call(workspace)
    assert result.status == 'failed' and 'do not support' in result.error['message']
    ordinary.assert_not_called()
    assert workspace.store.rows([WID])


def test_signed_rpc_preservation_uses_current_actor_and_confirms_index(workspace, tmp_path, monkeypatch):
    import threading
    from unittest.mock import Mock
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.transport import ResourceServiceClient
    from management_portal.resources.config import resource_rpc_signing_key_file
    from management_portal.resources.rpc_auth import load_rpc_signing_key, sign_rpc_auth
    from tests.cases.test_resource_service_process import FakePublisher
    from tests.cases.permission_test_support import enable, Fact
    prepare(workspace)
    link, _, directory = redirect(workspace)
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('workspace', WID))
    permissions.grant('alice', WID, '@admin')
    permissions.grant('bob', WID, '@contributor')
    projected = Mock()
    monkeypatch.setattr(workspace.service._index(), 'project_unregistered', projected)
    socket = tmp_path / 'resource.sock'
    key = load_rpc_signing_key(required=True, create=True, path=resource_rpc_signing_key_file(socket_path=socket))
    monkeypatch.setattr(ResourceServiceServer, '_start_warmup', lambda self: None)
    monkeypatch.setattr(ResourceServiceServer, '_start_background_maintenance', lambda self: None)
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    server = ResourceServiceServer(socket, service=workspace.service, publisher=FakePublisher())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    try:
        client = ResourceServiceClient(socket, timeout=3)
        with pytest.raises(PermissionError):
            client.unregister(WID, actor='bob', preserve_files=True)
        assert workspace.store.rows([WID])
        params = {'resource_id': WID, 'preserve_files': True, 'actor': 'alice'}
        with pytest.raises(PermissionError, match='assertion'):
            server.authenticate('resource.unregister', params,
                sign_rpc_auth(key, 'resource.unregister', params, actor='bob'))
        result = client.unregister(WID, actor='alice', preserve_files=True)
        assert result['attributes']['directory_retirement']['actor'] == 'alice'
        projected.assert_called_once_with('workspace', WID)
        assert not workspace.store.rows([WID]) and not workspace.store.cleanup.status(owner_id=WID)
        assert link.is_symlink() and directory.is_dir()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


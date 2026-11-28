"""Registration-only parent retirement preserves independent nested owners."""
import pytest

from management_common import directory_dependencies as deps
from management_portal.resources import directory_registrations
from management_portal.resources.registry_store import get_registry_store
from management_portal.society_service import resident_workspace_domain
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_workspace_redirected_retirement import workspace, resource_workspace, call, prepare
from tests.cases.test_workspace_resource_rename import WID
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload,
)

CHILD = 'workspace_111111111111'


def child_workspace(workspace):
    path = workspace.root / 'child'
    path.mkdir()
    (path / 'keep.txt').write_text('child content')
    workspace.store.ensure(CHILD, str(path))
    return path


def test_child_binding_owner_files_and_registry_survive(future_creation_store, workspace):
    path = child_workspace(workspace)
    resident = resident_domain.register_resident(_new_session_payload('Child user'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(path), 'sample_admin', workspace_id=CHILD)
    knowledge = 'knowledge_111111111111'
    directory_registrations.register_owned('knowledge', knowledge, [workspace.root / 'knowledge'])
    prepare(workspace)
    rows = workspace.store.rows([CHILD])
    registry = get_registry_store().get('knowledge', knowledge)
    with get_lineage_data_store().database.read() as connection:
        bindings = connection.execute('SELECT * FROM resident_workspace_bindings WHERE workspace_id=%s', (CHILD,)).fetchall()
    with workspace.store.database.read() as connection:
        dependencies = connection.execute(f'SELECT * FROM {deps.schema()}.directory_dependencies WHERE owner_id=ANY(%s)', ([CHILD, knowledge],)).fetchall()
    (workspace.root / 'shared-link').symlink_to(path, target_is_directory=True)
    inode = (workspace.root / 'shared-link').lstat().st_ino
    call(workspace).raise_for_error()
    assert not workspace.store.rows([WID])
    assert workspace.store.rows([CHILD]) == rows
    assert get_registry_store().get('knowledge', knowledge) == registry
    with get_lineage_data_store().database.read() as connection:
        assert connection.execute('SELECT * FROM resident_workspace_bindings WHERE workspace_id=%s', (CHILD,)).fetchall() == bindings
    with workspace.store.database.read() as connection:
        assert connection.execute(f'SELECT * FROM {deps.schema()}.directory_dependencies WHERE owner_id=ANY(%s)', ([CHILD, knowledge],)).fetchall() == dependencies
    assert workspace.call('ReadResource', {'address': CHILD + '/file/keep.txt'}).raise_for_error()['text'] == 'child content'
    assert (workspace.root / 'shared-link').lstat().st_ino == inode
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_child_permission_is_not_removed_or_granted_to_parent_admin(workspace, monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    child_workspace(workspace)
    prepare(workspace)
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('workspace', WID), Fact('workspace', CHILD))
    permissions.grant('alice', WID, '@admin')
    permissions.grant('bob', CHILD, '@viewer')
    permissions.grant('alice', CHILD, '@viewer', effect='deny')
    address = CHILD + '/file/keep.txt'
    denied_before = workspace.call('ReadResource', {'address': address}, actor='alice')
    assert denied_before.status == 'failed'
    call(workspace).raise_for_error()
    assert workspace.call('ReadResource', {'address': address}, actor='bob').raise_for_error()['text'] == 'child content'
    denied_after = workspace.call('ReadResource', {'address': address}, actor='alice')
    assert denied_after.status == 'failed'
    assert denied_after.error['code'] == denied_before.error['code']


def test_parent_owned_child_cannot_be_orphaned(workspace):
    path = child_workspace(workspace)
    row = workspace.store.rows([CHILD])[CHILD]
    with workspace.store.database.transaction() as connection:
        workspace.store.restore_owner(CHILD, owner_id=WID, expected_owner=CHILD,
            expected_version=row['updated_at'], expected_path=row['path'], connection=connection)
    prepare(workspace)
    before = workspace.store.rows([WID, CHILD])
    result = call(workspace)
    assert result.status == 'failed' and result.error['commit_state'] == 'not_committed'
    assert workspace.store.rows([WID, CHILD]) == before
    assert (path / 'keep.txt').read_text() == 'child content'


@pytest.mark.parametrize('placement', ['same', 'nested'])
def test_independent_registered_resource_remains(workspace, placement):
    rid = 'knowledge_222222222222'
    path = workspace.root if placement == 'same' else workspace.root / 'knowledge'
    directory_registrations.register_owned('knowledge', rid, [path])
    prepare(workspace)
    before = get_registry_store().get('knowledge', rid)
    call(workspace).raise_for_error()
    assert get_registry_store().get('knowledge', rid) == before
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_overlapping_child_operation_blocks_retirement_without_cancellation(workspace):
    prepare(workspace)
    with workspace.store.database.transaction() as connection:
        workspace.store.cleanup.reserve(workspace.root / 'new-child',
            operation_id='independent-child-creation', purpose='resource_creation', connection=connection)
        before = connection.execute(f'SELECT * FROM {workspace.store.cleanup.reservations}').fetchall()
    result = call(workspace)
    assert result.status == 'failed' and result.error['commit_state'] == 'not_committed'
    assert 'claims' in result.error['message']
    assert workspace.store.rows([WID])
    with workspace.store.database.read() as connection:
        assert connection.execute(f'SELECT * FROM {workspace.store.cleanup.reservations}').fetchall() == before
    assert not workspace.store.cleanup.status(owner_id=WID)


def test_preservation_never_schedules_discussion_identity_deletion(workspace, monkeypatch):
    prepare(workspace)
    def forbidden_projection_release(*args, **kwargs):
        raise AssertionError('Discussion deletion would violate file preservation')
    monkeypatch.setattr(directory_registrations, 'request_projection_release', forbidden_projection_release)
    result = call(workspace).raise_for_error()
    retirement = result['attributes']['directory_retirement']
    assert retirement['preserves_projections'] is True
    assert retirement['retired_nested_count'] == 0
    assert get_registry_store().get(directory_registrations.PROJECTION_RELEASES, WID) is None
    assert retirement['cleanup_ids'] == []


def test_retiring_two_parent_ids_keeps_grandchild_access(workspace):
    inner_path = child_workspace(workspace)
    leaf = 'workspace_333333333333'
    leaf_path = inner_path / 'leaf'
    leaf_path.mkdir()
    (leaf_path / 'keep.txt').write_text('two parents retired')
    workspace.store.ensure(leaf, str(leaf_path), owner_id='project_111111111111')
    prepare(workspace)
    before = workspace.store.rows([leaf])
    for parent in (CHILD, WID):
        result = workspace.call('UnregisterResource', {'resource_id': parent, 'preserve_files': True}).raise_for_error()
        assert result['attributes']['directory_retirement']['resource_ids'] == [parent]
        assert result['attributes']['directory_retirement']['cleanup_ids'] == []
        assert workspace.store.rows([leaf]) == before
        assert workspace.call('ReadResource', {'address': leaf + '/file/keep.txt'}).raise_for_error()['text'] == 'two parents retired'
        assert get_registry_store().get(directory_registrations.PROJECTION_RELEASES, parent) is None
    assert not workspace.store.rows([WID, CHILD])

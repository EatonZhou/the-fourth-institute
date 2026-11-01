"""Explicit orphan retirement through Resource ISA, with real references and PAR.

:module: resource.service
:design: decision_8838269736d3
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path

import pytest

from management_common.instance_locations import current_instance
from management_portal.resources.workspace_store import get_workspace_store, now
from management_portal.society_service import resident_workspace_domain
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload,
)
from tests.cases.permission_test_support import enable, Fact


@pytest.fixture
def workspace(resource_workspace):
    resource_workspace.root = resource_workspace.root / 'registered'
    resource_workspace.root.mkdir()
    resource_workspace.store.ensure(WID, str(resource_workspace.root), move=True)
    return resource_workspace


def call(workspace, wid=WID, actor='alice'):
    return workspace.call('UnregisterResource', {'resource_id': wid}, actor=actor)


def conflict(result, reason):
    assert result.status == 'failed', result.to_payload()
    assert result.error['code'] == 'instruction.conflict', result.to_payload()
    assert result.error['details']['reason'] == reason
    assert result.error['commit_state'] == 'not_committed'


@pytest.mark.parametrize('internal,exists', [(True, True), (False, True), (True, False), (False, False)])
def test_orphan_retirement_and_directory_boundary(workspace, internal, exists):
    root = (current_instance().area('projects') if internal else workspace.root) / 'orphan'
    workspace.store.ensure(WID, str(root), move=True)
    if exists:
        root.mkdir(parents=True)
        (root / 'evidence').write_text('keep outside')
    result = call(workspace).raise_for_error()
    assert result['resource_id'] == WID
    assert result['attributes']['status'] == 'deleted'
    assert 'path' not in result['attributes']
    assert workspace.store.rows([WID]) == {}
    row = workspace.store.rows([WID], include_deleted=True)[WID]
    assert row['deleted_at'] and row['deleted_by'] == 'alice'
    assert root.exists() == (exists and not internal)
    assert call(workspace).error['code'] == 'instruction.not_found'
    assert workspace.call('ResolveResource', {'address': WID}).error['code'] == 'instruction.not_found'


@pytest.mark.parametrize('usage', ['owner', 'binding'])
def test_live_resident_refuses_unregistration(future_creation_store, workspace, usage):
    resident = resident_domain.register_resident(_new_session_payload('Workspace user'), 'sample_admin')
    wid = WID
    if usage == 'owner':
        wid = resident['owned_workspace_id']
    else:
        resident_workspace_domain.add_resident_reference_workspace(
            resident['resident_id'], str(workspace.root), 'sample_admin', workspace_id=WID)
    before = workspace.store.rows([wid])
    result = call(workspace, wid)
    conflict(result, 'workspace_in_use')
    assert {'resident_id': resident['resident_id'], 'usage': usage} in result.error['details']['users']
    assert workspace.store.rows([wid]) == before


def test_deleted_resident_does_not_keep_binding_alive(future_creation_store, workspace):
    resident = resident_domain.register_resident(_new_session_payload('Former user'), 'sample_admin')
    resident_workspace_domain.add_resident_reference_workspace(
        resident['resident_id'], str(workspace.root), 'sample_admin', workspace_id=WID)
    # State between the identity deletion and its resumable lifecycle cleanup.
    with get_lineage_data_store().database.transaction() as conn:
        conn.execute('UPDATE residents SET deleted_at=%s WHERE resident_id=%s', (now(), resident['resident_id']))
    call(workspace).raise_for_error()
    with get_lineage_data_store().database.read() as conn:
        assert not conn.execute('SELECT 1 FROM resident_workspace_bindings WHERE workspace_id=%s', (WID,)).fetchall()


@pytest.mark.parametrize('deleted', [False, True])
def test_nested_registration_must_be_retired_first(workspace, deleted):
    child = 'workspace_aaaaaaaaaaaa'
    path = workspace.root / 'nested%_folder'
    path.mkdir()
    workspace.store.ensure(child, str(path))
    if deleted:
        with workspace.store.database.transaction() as conn:
            workspace.store.mark_deleted(child, deleted_at=now(), deleted_by='alice', connection=conn)
        call(workspace).raise_for_error()
    else:
        result = call(workspace)
        conflict(result, 'workspace_nested_registration')
        assert result.error['details']['resource_ids'] == [child]
        assert workspace.store.rows([WID, child]).keys() == {WID, child}
    assert path.exists()  # External host directories are never deleted here.


def test_older_internal_registration_can_be_unbound_and_retired(future_creation_store, workspace):
    """An instance directory outside the areas can no longer be registered;
    older registrations of one still let their Residents save, unbind and retire."""
    old = current_instance().root / 'maintenance' / 'old-gate'
    other = 'workspace_bbbbbbbbbbbb'
    stamp = now()
    with workspace.store.database.transaction() as conn:
        conn.execute(f'UPDATE {workspace.store.table} SET path=%s WHERE workspace_id=%s', (str(old / 'a'), WID))
        conn.execute(f'INSERT INTO {workspace.store.table} (workspace_id,path,title,created_at,updated_at) '
                     'VALUES (%s,%s,%s,%s,%s)', (other, str(old / 'b'), 'b', stamp, stamp))
    with pytest.raises(ValueError, match='Only area files'):
        workspace.store.ensure('workspace_cccccccccccc', str(old / 'c'))
    with pytest.raises(ValueError, match='Only area files'):
        workspace.store.ensure(other, str(old / 'moved'), move=True)
    resident = resident_domain.register_resident(_new_session_payload('Gate user'), 'sample_admin')
    with get_lineage_data_store().database.transaction() as conn:
        for wid in (WID, other):
            conn.execute('INSERT INTO resident_workspace_bindings(resident_id,workspace_id,purpose) VALUES (%s,%s,%s)',
                         (resident['resident_id'], wid, ''))
    conflict(call(workspace), 'workspace_in_use')
    # Unbinding saves the Resident, which keeps its other older binding as stored.
    resident_workspace_domain.remove_resident_reference_workspace(
        resident['resident_id'], 'sample_admin', workspace_id=WID)
    call(workspace).raise_for_error()
    assert workspace.store.rows([WID]) == {}
    assert workspace.store.rows([other])[other]['path'] == str(old / 'b')


def test_nonadmin_denied_by_real_permission_scheme(workspace, monkeypatch):
    permissions = enable(monkeypatch)
    permissions.users('alice', 'bob', category='intern')
    permissions.place(Fact('workspace', WID))
    permissions.grant('alice', WID, '@admin')
    permissions.grant('bob', WID, '@contributor')
    before = workspace.store.rows([WID])
    result = call(workspace, actor='bob')
    assert result.status == 'failed', result.to_payload()
    assert result.error['code'] == 'instruction.forbidden', result.to_payload()
    assert workspace.store.rows([WID]) == before
    # Also guard callers going directly through ResourceService, outside ISA.
    with pytest.raises(PermissionError):
        workspace.service.unregister(WID, actor='bob')
    call(workspace).raise_for_error()


def test_area_root_is_not_a_removable_workspace(workspace):
    root = current_instance().area('projects')
    workspace.store.ensure(WID, str(root), move=True)
    conflict(call(workspace), 'workspace_area_root')
    assert workspace.store.rows([WID])


def test_internal_cleanup_never_follows_child_symlinks(workspace):
    root = current_instance().area('projects') / 'orphan'
    root.mkdir(parents=True)
    evidence = workspace.root / 'evidence'
    evidence.write_text('outside')
    (root / 'link').symlink_to(evidence)
    workspace.store.ensure(WID, str(root), move=True)
    call(workspace).raise_for_error()
    assert not root.exists()
    assert evidence.read_text() == 'outside'


def test_cleanup_failure_keeps_registration_retryable(workspace, monkeypatch):
    from management_portal.society_service import resident_workspace_retirement as retirement
    root = current_instance().area('projects') / 'orphan'
    root.mkdir(parents=True)
    workspace.store.ensure(WID, str(root), move=True)
    original = retirement.remove_retired_folder
    def fail(*args, **kwargs):
        raise PermissionError('filesystem denied')
    monkeypatch.setattr(retirement, 'remove_retired_folder', fail)
    assert call(workspace).status == 'failed'
    assert workspace.store.rows([WID])
    monkeypatch.setattr(retirement, 'remove_retired_folder', original)
    call(workspace).raise_for_error()
    assert not root.exists()


def test_binding_committed_before_retirement_is_seen(future_creation_store, workspace):
    resident = resident_domain.register_resident(_new_session_payload('Concurrent user'), 'sample_admin')
    started = Event()
    def unregister():
        started.set()
        return call(workspace)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with get_lineage_data_store().database.transaction() as conn:
            workspace.store.ensure(WID, str(workspace.root), connection=conn)
            conn.execute('INSERT INTO resident_workspace_bindings(resident_id,workspace_id,purpose) VALUES (%s,%s,%s)',
                         (resident['resident_id'], WID, 'concurrent binding'))
            future = pool.submit(unregister)
            assert started.wait(2)
            assert not future.done()
        conflict(future.result(timeout=10), 'workspace_in_use')


@pytest.mark.parametrize('kind', ['repo', 'article', 'project'])
def test_other_directory_registrations_block_removal(workspace, kind):
    from management_portal.resources.registry_store import get_registry_store
    registry = get_registry_store()
    rid = kind + '_aaaaaaaaaaaa'
    if kind == 'project':
        project_id = 'nested-project'
        root = current_instance().area('projects')
        workspace.store.ensure(WID, str(root), move=True)
        collection, key = 'project_registry', 'projects'
        document = {'schema_version': 1, 'projects': [{'id': project_id}], 'tag_vocab': [], 'status_vocab': []}
        rid = project_id
    else:
        collection, key = kind, rid
        document = {'id': rid, 'canonical_checkout' if kind == 'repo' else 'canonical_root': str(workspace.root / 'child')}
    with registry.writer(collection) as writer:
        writer.put(collection, key, document)
    result = call(workspace)
    conflict(result, 'workspace_nested_registration')
    assert rid in result.error['details']['resource_ids']
    assert workspace.store.rows([WID])


def test_empty_live_lineage_directory_blocks_removal(future_creation_store, workspace, monkeypatch):
    from management_portal.society_service import project_folders
    (current_instance().area('projects') / 'project_test').mkdir(parents=True)
    monkeypatch.setattr(project_folders, 'project_root_for_id', lambda pid: current_instance().area('projects') / pid)
    resident = resident_domain.register_resident(_new_session_payload('Nested lineage'), 'sample_admin')
    with get_lineage_data_store().database.transaction() as conn:
        conn.execute('UPDATE residents SET deleted_at=%s WHERE resident_id=%s', (now(), resident['resident_id']))
        workspace.store.mark_deleted(resident['owned_workspace_id'], deleted_at=now(), deleted_by='alice', connection=conn)
    workspace.store.ensure(WID, resident['workspace_path'], move=True)
    result = call(workspace)
    conflict(result, 'workspace_nested_registration')
    assert resident['lineage_id'] in result.error['details']['resource_ids']

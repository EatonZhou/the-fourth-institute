"""Handoff relocation preserves its authoritative Lineage deletion dependency.

:module: resident.fork-handoff
:design: decision_4aa13bc21e8e, decision_7ef392b5f60d
"""
from pathlib import Path
import pytest

from tests.cases.directory_fixture_support import directory_owners  # noqa: F401
from tests.cases.test_lineage_backend import lineage_store  # noqa: F401
from tests.cases.test_handoff_directory_creation import draft
from management_common import directory_dependencies as deps
from management_common.directory_move_store import DirectoryMoveStore
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import (
    directory_relocation, handoff_location_owner as owner,
    lineage_handoffs, lineage_lifecycle, lineage_resident_workspaces,
)
from management_portal.society_service.state_store import get_lineage_data_store

pytestmark = pytest.mark.usefixtures('directory_owners')


def prepared(lineage_store, tmp_path, *, method='filesystem_copy', moved=True, legacy=False):
    lid, handoff, resident, source = draft(lineage_store, tmp_path, method)
    lineage_handoffs.execute_handoff(lid, handoff['id'], 'sample_admin')
    handoff = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']
    item = handoff['items'][0]
    wid = item['target']['workspace_id']
    store = get_workspace_store()
    row = store.rows([wid])[wid]
    assert row['owner_id'] == lid
    if moved:
        DirectoryMoveStore().move(Path(row['path']), tmp_path/'outside-copy',
            owner_id=lid, actor='sample_admin', terminate=lambda _: None,
            database=get_lineage_data_store().database,
            update=directory_relocation.relocate_nested,
            dependencies=directory_relocation.reconcile_dependencies)
        DirectoryMoveStore().cleanup.run_pending()
        row = store.rows([wid])[wid]
    if legacy:
        with store.database.transaction() as connection:
            store.restore_self_owner(wid, expected_version=row['updated_at'],
                expected_owner=row['owner_id'], expected_path=row['path'], connection=connection)
        row = store.rows([wid])[wid]
    snapshot = dict(workspace_id=wid, expected_version=row['updated_at'],
                    expected_owner=row['owner_id'], expected_path=row['path'],
                    operation_id='handoff-relocate-owner')
    return lid, handoff, item, resident, source, snapshot


def dependency(wid):
    store = get_workspace_store()
    with store.database.read() as connection:
        return dict(connection.execute(
            f"SELECT owner_id,location FROM {deps.schema()}.directory_dependencies "
            "WHERE kind='workspace' AND object_id=%s", (wid,)).fetchone())


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
@pytest.mark.parametrize('moved,legacy', [(True,False), (False,False), (False,True)])
def test_relocated_copy_keeps_lineage_owner_and_retirement_boundary(
        lineage_store, tmp_path, method, moved, legacy):
    lid, handoff, item, resident, source, snapshot = prepared(
        lineage_store, tmp_path, method=method, moved=moved, legacy=legacy)
    store = get_workspace_store()
    old = Path(snapshot['expected_path'])
    (old/'local-edit').write_text('retained edit')
    nested = old/'independent'; nested.mkdir()
    (nested/'keep').write_text('independent data')
    foreign = 'workspace_00000000ab91'
    store.ensure(foreign, str(nested))
    result = owner.relocate_copy(lid, handoff['id'], item['item_id'], 'sample_admin', **snapshot)
    row = result['workspace']; new = Path(row['path'])
    assert row['workspace_id'] == snapshot['workspace_id']
    assert row['owner_id'] == dependency(row['workspace_id'])['owner_id'] == lid
    assert (new/'local-edit').read_text() == 'retained edit'
    assert Path(source['path']).is_dir()
    assert store.rows([foreign])[foreign]['owner_id'] == foreign
    assert store.rows([foreign])[foreign]['path'] == str(new/'independent')
    if moved:
        assert not old.exists()
        assert DirectoryMoveStore().get(snapshot['operation_id'])['owner_id'] == lid
    current = lineage_handoffs.get_handoff(lid, handoff['id'], 'sample_admin')['handoff']['items'][0]
    assert current['attempts'] == item['attempts']
    assert current['target']['path'] == current['result']['target_path'] == str(new)
    bindings = lineage_resident_workspaces.get_node_workspaces(lid, resident, 'sample_admin')['workspaces']
    assert any(x['workspace_id'] == row['workspace_id'] and x['path'] == str(new) for x in bindings)
    lineage_lifecycle.delete_lineage(lid, 'sample_admin')
    DirectoryMoveStore().cleanup.run_pending()
    assert not store.rows([row['workspace_id']])
    assert not (new/'local-edit').exists()
    assert (new/'independent/keep').read_text() == 'independent data'


def test_registration_failure_rolls_back_owner_dependency_and_location(lineage_store, tmp_path, monkeypatch):
    from management_portal import directory_move_owner
    lid, handoff, item, _resident, _source, snapshot = prepared(lineage_store, tmp_path, legacy=True)
    before = dependency(snapshot['workspace_id'])
    original = directory_move_owner.relocate_nested
    def fail(*args):
        original(*args)
        raise RuntimeError('rollback ownership move')
    monkeypatch.setattr(directory_move_owner, 'relocate_nested', fail)
    with pytest.raises(RuntimeError, match='rollback ownership move'):
        owner.relocate_copy(lid, handoff['id'], item['item_id'], 'sample_admin', **snapshot)
    row = get_workspace_store().rows([snapshot['workspace_id']])[snapshot['workspace_id']]
    assert row['owner_id'] == snapshot['expected_owner']
    assert row['path'] == snapshot['expected_path'] and Path(row['path']).is_dir()
    assert dependency(row['workspace_id']) == before
    assert DirectoryMoveStore().get(snapshot['operation_id'])['phase'] == 'aborted'


@pytest.mark.parametrize('legacy_journal', [False, True])
def test_committed_pending_receipt_and_retry_keep_lineage_journal_owner(lineage_store, tmp_path, monkeypatch, legacy_journal):
    from management_portal import directory_move_owner
    lid, handoff, item, _resident, _source, snapshot = prepared(lineage_store, tmp_path)
    if legacy_journal:
        original_move = directory_relocation.move_idle_directory
        def legacy_move(*args, **kwargs):
            # A retained pre-upgrade operation used the Workspace ID as owner.
            return original_move(*args, **{**kwargs, 'owner_id': snapshot['workspace_id']})
        monkeypatch.setattr(directory_relocation, 'move_idle_directory', legacy_move)
    original = directory_move_owner.reconcile_dependencies
    def fail(*args):
        raise OSError('native move dependency pending')
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', fail)
    result = owner.relocate_registered_copy(lid, handoff['id'], item['item_id'], 'sample_admin',
        workspace_id=snapshot['workspace_id'], expected_version=snapshot['expected_version'],
        operation_id=snapshot['operation_id'])
    assert result['status'] == 'pending'
    assert result['workspace']['owner_id'] == lid
    assert DirectoryMoveStore().get(snapshot['operation_id'])['owner_id'] == (snapshot['workspace_id'] if legacy_journal else lid)
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', original)
    DirectoryMoveStore().finish(snapshot['operation_id'], dependencies=directory_relocation.reconcile_dependencies)
    assert DirectoryMoveStore().get(snapshot['operation_id'])['phase'] == 'completed'
    assert dependency(snapshot['workspace_id'])['owner_id'] == lid


@pytest.mark.parametrize('failure', ['stale', 'denied'])
def test_stale_or_denied_move_does_not_transfer_ownership(lineage_store, tmp_path, monkeypatch, failure):
    from management_portal import permission_system
    from management_portal.resources.errors import ResourceVersionConflict
    lid, handoff, item, _resident, _source, snapshot = prepared(lineage_store, tmp_path, legacy=True)
    before = dependency(snapshot['workspace_id'])
    if failure == 'stale':
        options = {**snapshot, 'expected_version': 'stale'}
        error = ResourceVersionConflict
    else:
        options = snapshot
        monkeypatch.setattr(permission_system, 'permitted_ids', lambda *args, **kwargs: [])
        error = PermissionError
    with pytest.raises(error):
        owner.relocate_copy(lid, handoff['id'], item['item_id'], 'sample_admin', **options)
    assert dependency(snapshot['workspace_id']) == before
    assert Path(snapshot['expected_path']).is_dir()


@pytest.mark.parametrize('mismatch', ['owner', 'actor'])
def test_pending_receipt_rejects_unrelated_journal(lineage_store, tmp_path, monkeypatch, mismatch):
    from management_portal import directory_move_owner
    lid, handoff, item, _resident, _source, snapshot = prepared(lineage_store, tmp_path)
    original_move = directory_relocation.move_idle_directory
    original_dependencies = directory_move_owner.reconcile_dependencies
    def altered_move(*args, **kwargs):
        key = 'owner_id' if mismatch == 'owner' else 'actor'
        return original_move(*args, **{**kwargs, key: 'unrelated-identity'})
    def fail(*args):
        raise OSError('unrelated pending journal')
    monkeypatch.setattr(directory_relocation, 'move_idle_directory', altered_move)
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', fail)
    with pytest.raises(RuntimeError, match='unrelated pending journal'):
        owner.relocate_registered_copy(lid, handoff['id'], item['item_id'], 'sample_admin',
            workspace_id=snapshot['workspace_id'], expected_version=snapshot['expected_version'],
            operation_id=snapshot['operation_id'])
    journal = DirectoryMoveStore().get(snapshot['operation_id'])
    assert journal['phase'] == 'committed'
    assert journal['owner_id' if mismatch == 'owner' else 'actor'] == 'unrelated-identity'
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', original_dependencies)
    DirectoryMoveStore().finish(snapshot['operation_id'], dependencies=directory_relocation.reconcile_dependencies)
    assert dependency(snapshot['workspace_id'])['owner_id'] == lid

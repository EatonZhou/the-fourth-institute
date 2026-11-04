"""File-first moves enlist every registration and recover durable phases.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common.directory_move_store import DirectoryMoveStore
from management_portal.directory_move_owner import relocate_nested, repair_git_worktrees
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID


def test_move_commits_nested_locations_and_old_cleanup_together(workspace):
    from management_portal.resources.registry_store import get_registry_store
    registry = get_registry_store()
    rid = 'repo_112233445566'
    with registry.writer('repo') as writer:
        writer.put('repo', rid, {'id': rid, 'checkouts': [{'path': str(workspace.root / 'repo')}]})
    target = workspace.root.with_name('moved')
    mover = DirectoryMoveStore()
    operation_id, _ = mover.move(workspace.root, target, owner_id='owner', actor='sample_admin',
        terminate=lambda _: pytest.fail('same filesystem'), database=get_lineage_data_store().database,
        update=relocate_nested, dependencies=lambda *_: None)
    assert mover.get(operation_id)['phase'] == 'completed'
    assert workspace.store.rows([WID])[WID]['path'] == str(target)
    assert registry.get('repo', rid)['checkouts'][0]['path'] == str(target / 'repo')
    assert mover.cleanup.pending_at(workspace.root)
    mover.cleanup.run_pending()
    assert target.is_dir()


def test_precommit_restart_restores_rename_without_moving_registration(workspace):
    target = workspace.root.with_name('moved')
    mover = DirectoryMoveStore()
    op = mover.prepare(workspace.root, target, owner_id='owner', actor='sample_admin', terminate=lambda _: None)
    assert target.exists() and not workspace.root.exists()
    DirectoryMoveStore().recover(dependencies=lambda *_: pytest.fail('uncommitted'))
    assert workspace.root.exists() and not target.exists()
    assert mover.get(op)['phase'] == 'aborted'
    assert not mover.cleanup.pending_at(workspace.root)


def test_dependency_failure_never_rolls_back_committed_move(workspace):
    target = workspace.root.with_name('moved')
    mover = DirectoryMoveStore()
    def fail(*_):
        raise OSError('provider occupied')
    with pytest.raises(RuntimeError, match='provider occupied'):
        mover.move(workspace.root, target, owner_id='owner', actor='sample_admin',
            terminate=lambda _: None, database=get_lineage_data_store().database,
            update=relocate_nested, dependencies=fail)
    assert target.exists() and not workspace.root.exists()
    outcome, = DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert outcome['phase'] == 'completed'


def test_source_cannot_be_reused_before_dependency_recovery(workspace):
    mover = DirectoryMoveStore()
    def fail(*_):
        raise OSError('provider occupied')
    with pytest.raises(RuntimeError, match='provider occupied'):
        mover.move(workspace.root, workspace.root.with_name('moved'), owner_id='owner',
            actor='sample_admin', terminate=lambda _: None, database=get_lineage_data_store().database,
            update=relocate_nested, dependencies=fail)
    mover.cleanup.run_pending()
    with pytest.raises(FileExistsError, match='reserved'):
        mover.database.write(lambda c: mover.cleanup.assert_available(workspace.root, connection=c))
    mover.recover(dependencies=lambda *_: None)
    mover.cleanup.run_pending()
    mover.database.write(lambda c: mover.cleanup.assert_available(workspace.root, connection=c))


def test_move_accepts_existing_owned_directory_without_source_classification(workspace):
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('moved')
    (workspace.root / 'payload').write_text('existing data')
    operation = mover.prepare(workspace.root, target, owner_id='owner',
                              actor='sample_admin', terminate=lambda _: None)
    assert (target / 'payload').read_text() == 'existing data'
    assert mover.get(operation)['phase'] == 'files_ready'
    assert 'origin' not in mover.get(operation)
    mover.abort(operation)
    assert (workspace.root / 'payload').read_text() == 'existing data'


def test_git_worktree_external_backlink_follows_move(tmp_path):
    old, target, admin = tmp_path / 'old', tmp_path / 'new', tmp_path / 'repo' / '.git' / 'worktrees' / 'branch'
    target.mkdir()
    admin.mkdir(parents=True)
    (target / '.git').write_text('gitdir: ' + str(admin) + '\n')
    (admin / 'gitdir').write_text(str(old / '.git') + '\n')
    repair_git_worktrees(old, target)
    assert (admin / 'gitdir').read_text().strip() == str(target / '.git')


def test_cross_filesystem_copy_cancels_before_copy_and_recovers_partial_target(workspace, monkeypatch):
    import os
    import shutil
    stat = Path.stat
    def different_device(path, *args, **kwargs):
        result = stat(path, *args, **kwargs)
        if path == workspace.root:
            values = list(result)
            values[2] += 1
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'stat', different_device)
    events = []
    copytree = shutil.copytree
    def copy(source, target, **kwargs):
        assert events == ['cancelled']
        return copytree(source, target, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', copy)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('copy')
    op = mover.prepare(workspace.root, target, owner_id='owner', actor='sample_admin',
                       terminate=lambda _: events.append('cancelled'))
    assert workspace.root.exists() and target.exists()
    mover.recover(dependencies=lambda *_: None)
    assert workspace.root.exists() and not target.exists()
    assert mover.get(op)['phase'] == 'aborted'


def test_registration_failure_rolls_back_nested_paths_and_restores_source(workspace):
    target = workspace.root.with_name('moved')
    def update(connection, source, target):
        relocate_nested(connection, source, target)
        raise RuntimeError('identity conflict')
    with pytest.raises(RuntimeError, match='identity conflict'):
        DirectoryMoveStore().move(workspace.root, target, owner_id='owner', actor='sample_admin',
            terminate=lambda _: None, database=get_lineage_data_store().database, update=update, dependencies=lambda *_: None)
    assert workspace.root.exists() and not target.exists()
    assert workspace.store.rows([WID])[WID]['path'] == str(workspace.root)


def test_retirement_removes_all_registered_external_directories(workspace):
    from scripts.directory_protocol_retire import cleanup_external_owned
    first = workspace.root.with_name('external-first')
    second = workspace.root.with_name('external-second')
    first.mkdir()
    second.mkdir()
    workspace.store.ensure('workspace_998877665544', str(first))
    workspace.store.ensure('workspace_887766554433', str(second))
    cleanup_external_owned()
    assert not first.exists()
    assert not second.exists()


def test_retirement_recovers_uncommitted_external_move_before_inventory(workspace):
    from scripts.directory_protocol_retire import cleanup_external_owned
    target = workspace.root.with_name('pending-move')
    mover = DirectoryMoveStore()
    operation = mover.prepare(workspace.root, target, owner_id='owner',
                              actor='sample_admin', terminate=lambda _: None)
    cleanup_external_owned()
    assert not target.exists() and not workspace.root.exists()
    assert mover.get(operation)['phase'] == 'aborted'

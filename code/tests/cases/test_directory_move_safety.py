"""Move publication races and retirement path reuse preserve host files.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
import os
from pathlib import Path
import shutil

import pytest

from management_common.directory_move_store import DirectoryMoveStore
from management_common.operation_errors import OperationError
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID


def cross_device(monkeypatch, source):
    original = Path.stat
    def stat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == source:
            values = list(result)
            values[2] += 1
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, 'stat', stat)


def prepare(mover, source, target, **kwargs):
    return mover.prepare(source, target, owner_id='owner', origin='created',
                         actor='sample_admin', terminate=lambda _: None, **kwargs)


def test_same_filesystem_racing_empty_target_is_never_replaced(workspace, monkeypatch):
    target = workspace.root.with_name('racing-empty')
    (workspace.root / 'source-file').write_text('source')
    mkdir = Path.mkdir
    identity = []
    def race(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if path == target.parent and not target.exists():
            mkdir(target)
            identity.append(target.stat().st_ino)
        return result
    monkeypatch.setattr(Path, 'mkdir', race)
    with pytest.raises(FileExistsError):
        prepare(DirectoryMoveStore(), workspace.root, target)
    assert target.stat().st_ino == identity[0]
    assert list(target.iterdir()) == []
    assert (workspace.root / 'source-file').read_text() == 'source'


@pytest.mark.parametrize('interrupted', [False, True])
def test_copy_racing_nonempty_target_keeps_every_existing_file(workspace, monkeypatch, interrupted):
    cross_device(monkeypatch, workspace.root)
    target = workspace.root.with_name('racing-copy')
    (workspace.root / 'source-file').write_text('source')
    original = shutil.copytree
    def race(source, destination, **kwargs):
        target.mkdir()
        (target / 'host-file').write_text('host')
        if interrupted:
            Path(destination).mkdir(exist_ok=True)
            (Path(destination) / 'partial').write_text('partial')
            raise OSError('copy interrupted')
        return original(source, destination, **kwargs)
    monkeypatch.setattr(shutil, 'copytree', race)
    with pytest.raises(OSError):
        prepare(DirectoryMoveStore(), workspace.root, target)
    assert (workspace.root / 'source-file').read_text() == 'source'
    assert (target / 'host-file').read_text() == 'host'
    assert not list(target.parent.glob('.tfi-move-*'))


@pytest.mark.parametrize('phase', ['prepared', 'files_ready', 'aborted', 'committed', 'completed', 'cancelled'])
def test_fixed_operation_retry_reports_original_state_without_mutation(workspace, phase):
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('retry')
    operation = prepare(mover, workspace.root, target, operation_id='fixed-move')
    if phase == 'prepared':
        mover._phase(operation, 'prepared')
    elif phase == 'aborted':
        mover.abort(operation)
    elif phase in {'committed', 'completed', 'cancelled'}:
        mover.database.write(lambda c: mover.commit(operation, connection=c, update=lambda *_: None))
        if phase == 'completed':
            mover.finish(operation, dependencies=lambda *_: None)
        elif phase == 'cancelled':
            from management_common.directory_move_store import cancel_owner_moves
            mover.database.write(lambda c: cancel_owner_moves(c, 'owner'))
    with pytest.raises(OperationError) as error:
        prepare(DirectoryMoveStore(), workspace.root, target, operation_id=operation)
    assert error.value.code == 'directory.move_operation_exists'
    assert error.value.details == {'operation_id': operation, 'phase': phase}
    assert error.value.commit_state == ('committed' if phase in {'committed', 'completed', 'cancelled'} else 'not_committed')
    assert mover.get(operation)['phase'] == phase


def test_retirement_does_not_delete_adopted_reuse_of_created_tombstone(workspace):
    from scripts.directory_protocol_retire import cleanup_external_created
    from management_portal.resources.workspace_store import now
    root = workspace.root
    workspace.store.ensure(WID, str(root), origin='created')
    with workspace.store.database.transaction() as connection:
        workspace.store.mark_deleted(WID, deleted_at=now(), deleted_by='sample_admin', connection=connection)
    workspace.store.ensure('workspace_aaaaaaaaaaaa', str(root), origin='adopted')
    (root / 'host-account-credentials').write_text('host data')
    cleanup_external_created()
    assert (root / 'host-account-credentials').read_text() == 'host data'


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_retirement_preserves_host_account_reusing_created_tombstone(workspace, provider):
    from scripts.directory_protocol_retire import cleanup_external_created
    from management_portal.execution import account_registrations
    from management_portal.resources.workspace_store import now
    root = workspace.root
    workspace.store.ensure(WID, str(root), origin='created')
    with workspace.store.database.transaction() as connection:
        workspace.store.mark_deleted(WID, deleted_at=now(), deleted_by='sample_admin', connection=connection)
    row = account_registrations.create_registration({
        'account_id': provider + '_retire_test', 'provider': provider,
        'kind': 'subscription_login', 'config': {'account_dir': str(root)},
    }, 'sample_admin')
    assert row['directory_origin'] == 'adopted' and row['directory_owner'] == 'host'
    (root / 'account-data').write_text('test host account data')
    cleanup_external_created()
    assert (root / 'account-data').read_text() == 'test host account data'


def test_copy_dependencies_complete_before_old_directory_cleanup(workspace, monkeypatch):
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('copy-dependencies')
    operation = prepare(mover, workspace.root, target)
    mover.database.write(lambda c: mover.commit(operation, connection=c, update=lambda *_: None))
    def pending(*_):
        raise OSError('dependency pending')
    with pytest.raises(OperationError):
        mover.finish(operation, dependencies=pending)
    mover.cleanup.run_pending()
    assert workspace.root.is_dir() and target.is_dir()
    DirectoryMoveStore().recover(dependencies=lambda *_: None)
    mover.cleanup.run_pending()
    assert not workspace.root.exists() and target.is_dir()


@pytest.mark.parametrize('crash_point', ['copy', 'published'])
def test_new_store_recovers_copy_crash_without_touching_unrelated_target(workspace, monkeypatch, crash_point):
    cross_device(monkeypatch, workspace.root)
    target = workspace.root.with_name('crash-copy')
    (workspace.root / '.tfi-directory-creation').write_text('user marker filename')
    (workspace.root / 'operation').write_text('user operation filename')
    mover = DirectoryMoveStore()
    # Simulate process death: no in-process abort is possible. Recovery must
    # obtain all operation identity from a new store, not an in-memory flag.
    monkeypatch.setattr(mover, 'abort', lambda *_: None)
    if crash_point == 'copy':
        def interrupted(source, destination, **kwargs):
            Path(destination).mkdir()
            (Path(destination) / 'partial').write_text('unfinished')
            target.mkdir()
            (target / 'host-file').write_text('host')
            raise SystemExit('process killed during copy')
        monkeypatch.setattr(shutil, 'copytree', interrupted)
    else:
        monkeypatch.setattr(mover, '_phase', lambda *_: (_ for _ in ()).throw(SystemExit('process killed after publish')))
    with pytest.raises(SystemExit):
        prepare(mover, workspace.root, target, operation_id='crashed-copy')
    row, = DirectoryMoveStore().recover(dependencies=lambda *_: pytest.fail('uncommitted'))
    assert row['phase'] == 'aborted'
    assert (workspace.root / 'operation').read_text() == 'user operation filename'
    assert (workspace.root / '.tfi-directory-creation').read_text() == 'user marker filename'
    if crash_point == 'copy':
        assert (target / 'host-file').read_text() == 'host'
    else:
        assert not target.exists()
    assert not list(target.parent.glob('.tfi-move-*'))


def test_copy_preserves_user_marker_filenames_and_permissions(workspace, monkeypatch):
    cross_device(monkeypatch, workspace.root)
    for name in ['operation', '.tfi-directory-creation', '.tfi-directory-move']:
        (workspace.root / name).write_text('user ' + name)
    before_mode = workspace.root.stat().st_mode
    target = workspace.root.with_name('user-files')
    prepare(DirectoryMoveStore(), workspace.root, target)
    for name in ['operation', '.tfi-directory-creation', '.tfi-directory-move']:
        assert (target / name).read_text() == 'user ' + name
    assert target.stat().st_mode == before_mode


@pytest.mark.parametrize('mode', ['rename', 'copy'])
def test_abort_does_not_remove_replaced_destination(workspace, monkeypatch, mode):
    if mode == 'copy':
        cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('replaced')
    operation = prepare(mover, workspace.root, target)
    saved = target.with_name('saved-operation-files')
    target.rename(saved)
    target.mkdir()
    (target / 'host-file').write_text('host')
    with pytest.raises(FileExistsError, match='replaced'):
        mover.database.write(lambda c: mover.commit(operation, connection=c, update=lambda *_: pytest.fail('foreign target')))
    DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert (target / 'host-file').read_text() == 'host'
    assert saved.is_dir()
    assert mover.get(operation)['phase'] == ('aborted' if mode == 'copy' else 'files_ready')


def test_abort_rename_does_not_overwrite_new_source(workspace):
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('new-source')
    operation = prepare(mover, workspace.root, target)
    workspace.root.mkdir()
    (workspace.root / 'host-file').write_text('host')
    outcome, = DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert outcome['phase'] == 'files_ready'
    assert (workspace.root / 'host-file').read_text() == 'host'
    assert target.is_dir()


@pytest.mark.parametrize('argument', ['actor', 'owner_id', 'target'])
def test_operation_id_cannot_be_reused_for_different_arguments(workspace, argument):
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('same-operation')
    operation = prepare(mover, workspace.root, target)
    arguments = dict(source=workspace.root, target=target, owner_id='owner', actor='sample_admin',
                     origin='created', operation_id=operation, terminate=lambda _: pytest.fail('retry side effect'))
    arguments[argument] = target.with_name('other') if argument == 'target' else 'other'
    with pytest.raises(ValueError, match='different arguments'):
        mover.prepare(**arguments)
    assert target.is_dir() and mover.get(operation)['phase'] == 'files_ready'


def test_deleted_owner_still_cleans_old_copy_after_dependency_failure(workspace, monkeypatch):
    from management_common.directory_move_store import cancel_owner_moves
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('cancelled-copy')
    operation = prepare(mover, workspace.root, target)
    mover.database.write(lambda c: mover.commit(operation, connection=c, update=lambda *_: None))
    mover.database.write(lambda c: cancel_owner_moves(c, 'owner'))
    mover.cleanup.run_pending()
    assert mover.get(operation)['phase'] == 'cancelled'
    assert not workspace.root.exists() and target.is_dir()


@pytest.mark.parametrize('target', ['relative', '/tmp/invalid\x00name', '/tmp/parent/../child'])
def test_invalid_filename_rejected_before_file_effects(workspace, target):
    with pytest.raises(ValueError):
        prepare(DirectoryMoveStore(), workspace.root, target)
    assert workspace.root.is_dir()


def test_case_only_move_rejection_preserves_source_on_insensitive_filesystem(workspace, monkeypatch):
    from management_common import directory_paths
    monkeypatch.setattr(directory_paths, '_directory_case_sensitive', lambda _: False)
    target = workspace.root.with_name(workspace.root.name.upper())
    (workspace.root / 'keep').write_text('source')
    with pytest.raises(FileExistsError):
        prepare(DirectoryMoveStore(), workspace.root, target)
    assert (workspace.root / 'keep').read_text() == 'source'


def test_retirement_drains_old_copy_when_dependencies_were_pending(workspace, monkeypatch):
    from management_portal.directory_move_owner import relocate_nested
    from scripts.directory_protocol_retire import cleanup_external_created
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('retire-pending-copy')
    workspace.store.ensure(WID, str(workspace.root), origin='created')
    operation = prepare(mover, workspace.root, target)
    mover.database.write(lambda c: mover.commit(operation, connection=c, update=relocate_nested))
    cleanup_external_created()
    assert not workspace.root.exists() and not target.exists()
    assert mover.get(operation)['phase'] == 'cancelled'


def test_recovery_preserves_unowned_staging_entry(workspace, monkeypatch):
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    monkeypatch.setattr(mover, 'abort', lambda *_: None)
    target = workspace.root.with_name('stage-race')
    mkdir = Path.mkdir
    def race(path, *args, **kwargs):
        if path.name.startswith('.tfi-move-'):
            mkdir(path)
            (path / 'host-file').write_text('host')
        return mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', race)
    with pytest.raises(FileExistsError):
        prepare(mover, workspace.root, target, operation_id='stage-race')
    outcome, = DirectoryMoveStore().recover(dependencies=lambda *_: None)
    from management_common.directory_paths import cleanup_path
    stage = cleanup_path(outcome['staging'])
    assert outcome['phase'] == 'aborted' and 'ownership' in outcome['last_error']
    assert (stage / 'host-file').read_text() == 'host'
    assert workspace.root.is_dir()
    with pytest.raises(FileExistsError, match='reserved'):
        mover.database.write(lambda c: mover.cleanup.assert_available(stage, connection=c))


def test_retirement_uses_pending_cleanup_but_not_completed_snapshot(workspace):
    from scripts.directory_protocol_retire import cleanup_external_created
    mover = DirectoryMoveStore()
    pending = workspace.root.with_name('unregistered-pending')
    completed = workspace.root.with_name('old-completed')
    pending.mkdir()
    completed.mkdir()
    def enqueue(connection):
        first = mover.cleanup.enqueue(completed, owner_id='old', operation_id='completed', actor='actor', connection=connection)
        second = mover.cleanup.enqueue(pending, owner_id='pending', operation_id='pending', actor='actor', connection=connection)
        return first, second
    first, _ = mover.database.write(enqueue)
    mover.cleanup.run_pending(cleanup_id=first)
    completed.mkdir()
    (completed / 'host-file').write_text('new owner')
    workspace.store.ensure('workspace_bbbbbbbbbbbb', str(completed), origin='adopted')
    cleanup_external_created()
    assert not pending.exists()
    assert (completed / 'host-file').read_text() == 'new owner'

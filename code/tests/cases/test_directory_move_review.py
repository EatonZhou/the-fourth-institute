"""Independent-review regressions for move ownership and recovery.

:module: resident.workspace-memory
:design: decision_4aa13bc21e8e
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from management_common.directory_move_store import DirectoryMoveStore
from management_common.directory_paths import cleanup_location, cleanup_path
from management_common.directory_retention import RetentionStore
from management_portal.directory_move_owner import relocate_nested
from tests.cases.test_directory_move_safety import cross_device
from tests.cases.test_workspace_unregistration import workspace
from tests.cases.test_workspace_resource_rename import workspace as resource_workspace, WID


def test_move_retention_must_not_delete_reused_source(workspace):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    now = datetime.now(timezone.utc)
    retention = RetentionStore()
    old_file = workspace.root / 'output.txt'
    old_file.write_text('old artifact')
    retention.declare(old_file, category='background', owner_id='job-old',
                      created_at=now, finished_at=now)
    target = workspace.root.with_name('moved')
    mover = DirectoryMoveStore()
    mover.move(workspace.root, target, owner_id=WID, origin='created', actor='alice',
               terminate=lambda _: None, database=workspace.store.database,
               update=relocate_nested, dependencies=lambda *_: None)
    mover.cleanup.run_pending()
    WorkspaceCreationStore().create(path=workspace.root, owner_id='', actor='alice',
                                    operation_id='new-source')
    old_file.write_text('NEW unrelated artifact')
    retention.sweep(now=now + timedelta(days=8))
    mover.cleanup.run_pending()
    assert old_file.exists(), 'old retention record deleted NEW file at reused source'
    assert not (target / 'output.txt').exists(), 'moved expired artifact was not removed'


def test_move_crash_between_stage_mkdir_and_marker_recovers(workspace, monkeypatch):
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('copy-target')
    original_open = Path.open
    def crash(path, *args, **kwargs):
        if path.name == 'operation' and path.parent.name.startswith('.tfi-move-'):
            raise SystemExit('crash after staging mkdir before marker')
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', crash)
        patch.setattr(mover, 'abort', lambda *_: None)
        with pytest.raises(SystemExit):
            mover.prepare(workspace.root, target, owner_id=WID, origin='created',
                          actor='alice', terminate=lambda _: None, operation_id='stage-gap')
    result, = DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert result['phase'] == 'aborted', result
    assert workspace.root.is_dir() and not target.exists()


def test_unproven_stage_aborts_preserving_files_and_allows_new_move(workspace, monkeypatch):
    from management_common import directory_move_store as module
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('copy-target')
    original_identity = module._directory_identity
    def crash(path):
        if path.name.startswith('.tfi-move-'):
            (path / 'unknown-file').write_text('must keep')
            raise SystemExit('mkdir completed; no inode has been saved')
        return original_identity(path)
    with monkeypatch.context() as patch:
        patch.setattr(module, '_directory_identity', crash)
        patch.setattr(mover, 'abort', lambda *_: None)
        with pytest.raises(SystemExit):
            mover.prepare(workspace.root, target, owner_id=WID, origin='created',
                          actor='alice', terminate=lambda _: None, operation_id='no-proof')
    result, = DirectoryMoveStore().recover(dependencies=lambda *_: pytest.fail('uncommitted'))
    assert result['phase'] == 'aborted' and 'ownership' in result['last_error']
    assert not result['staging_identity']
    stage = cleanup_path(result['staging'])
    assert (stage / 'unknown-file').read_text() == 'must keep'
    with pytest.raises(FileExistsError, match='reserved'):
        mover.database.write(lambda c: mover.cleanup.assert_available(stage, connection=c))
    mover.move(workspace.root, target, owner_id=WID, origin='created', actor='alice',
               terminate=lambda _: None, database=workspace.store.database,
               update=relocate_nested, dependencies=lambda *_: None, operation_id='next-move')
    mover.cleanup.run_pending()
    assert (stage / 'unknown-file').read_text() == 'must keep'
    assert target.is_dir() and not workspace.root.exists()
    assert DirectoryMoveStore().recover(dependencies=lambda *_: None) == []


def test_copied_marker_does_not_prove_replaced_staging(workspace, monkeypatch):
    cross_device(monkeypatch, workspace.root)
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('copy-target')
    def replace_stage(source, payload, **kwargs):
        stage = payload.parent
        marker = (stage / 'operation').read_text()
        stage.rename(stage.with_name('saved-original-stage'))
        stage.mkdir()
        (stage / 'operation').write_text(marker)
        (stage / 'host-file').write_text('host')
        raise SystemExit('stage replaced by foreign inode')
    with monkeypatch.context() as patch:
        patch.setattr('shutil.copytree', replace_stage)
        patch.setattr(mover, 'abort', lambda *_: None)
        with pytest.raises(SystemExit):
            mover.prepare(workspace.root, target, owner_id=WID, origin='created',
                          actor='alice', terminate=lambda _: None)
    result, = DirectoryMoveStore().recover(dependencies=lambda *_: None)
    assert result['phase'] == 'aborted'
    stage = cleanup_path(result['staging'])
    assert (stage / 'host-file').read_text() == 'host'
    assert 'ownership' in result['last_error']


@pytest.mark.parametrize('mode', ['rename', 'copy', 'external'])
def test_retention_metadata_and_link_location_follow_move(workspace, monkeypatch, mode):
    if mode == 'copy':
        cross_device(monkeypatch, workspace.root)
    target = workspace.root.with_name('moved')
    foreign = workspace.root.with_name('foreign')
    foreign.write_text('must keep')
    link = workspace.root / 'artifact-link'
    link.symlink_to(foreign)
    retention = RetentionStore()
    now = datetime.now(timezone.utc)
    retention.declare(link, category='background', owner_id='job', finished_at=now, bytes=9)
    with retention.database.read() as c:
        before = dict(c.execute(f'SELECT * FROM {retention.table}').fetchone())
    mover = DirectoryMoveStore()
    kwargs = dict(owner_id=WID, actor='alice', database=workspace.store.database,
                  update=relocate_nested, dependencies=lambda *_: None)
    if mode == 'external':
        workspace.root.rename(target)
        mover.register_external_move(workspace.root, target, **kwargs)
    else:
        mover.move(workspace.root, target, origin='created', terminate=lambda _: None, **kwargs)
    with retention.database.read() as c:
        after = dict(c.execute(f'SELECT * FROM {retention.table}').fetchone())
    assert after == {**before, 'location': cleanup_location(target / link.name)}
    retention.sweep(now=now + timedelta(days=8))
    mover.cleanup.run_pending()
    assert foreign.read_text() == 'must keep'
    assert not (target / link.name).is_symlink()


@pytest.mark.parametrize('rollback', [False, True])
def test_pending_cleanup_and_retention_share_registration_transaction(workspace, rollback):
    retention = RetentionStore()
    first, second = workspace.root / 'expire', workspace.root / 'retain'
    first.write_text('expired')
    second.write_text('retain')
    now = datetime.now(timezone.utc)
    retention.declare(first, category='background', owner_id='expired-job', finished_at=now)
    retention.declare(second, category='object_data', owner_id='live-job')
    mover = DirectoryMoveStore()
    target = workspace.root.with_name('moved')
    operation = mover.prepare(workspace.root, target, owner_id=WID, origin='created',
                              actor='alice', terminate=lambda _: None)
    # An owner enqueues cleanup after prepare. The intent follows moved files
    # only when registration commits; historical completed receipts stay put.
    retention.sweep(now=now + timedelta(days=8))
    def update(c, source, destination):
        relocate_nested(c, source, destination)
        if rollback:
            raise RuntimeError('registration failed')
    if rollback:
        with pytest.raises(RuntimeError, match='registration failed'):
            mover.database.write(lambda c: mover.commit(operation, connection=c, update=update))
        mover.abort(operation)
    else:
        mover.database.write(lambda c: mover.commit(operation, connection=c, update=update))
        mover.finish(operation, dependencies=lambda *_: None)
    expected = workspace.root if rollback else target
    with retention.database.read() as c:
        row = c.execute(f'SELECT location FROM {retention.table}').fetchone()
    assert row['location'] == cleanup_location(expected / second.name)
    pending = [row for row in mover.cleanup.status() if row['owner_id'] == 'expired-job']
    assert pending[0]['location'] == cleanup_location(expected / first.name)
    mover.cleanup.run_pending()
    assert not (expected / first.name).exists()
    assert (expected / second.name).read_text() == 'retain'


def test_pending_cleanup_blocks_new_move_before_files_change(workspace):
    mover = DirectoryMoveStore()
    artifact = workspace.root / 'expired'
    artifact.write_text('expired')
    mover.database.write(lambda c: mover.cleanup.enqueue(artifact, owner_id='job',
        operation_id='expire', actor='alice', connection=c))
    target = workspace.root.with_name('moved')
    with pytest.raises(FileExistsError, match='pending cleanup'):
        mover.prepare(workspace.root, target, owner_id=WID, origin='created',
                      actor='alice', terminate=lambda _: None)
    assert artifact.exists() and not target.exists()


def test_completed_creation_receipt_stays_historical_after_move_and_reuse(workspace):
    from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
    creator = WorkspaceCreationStore()
    nested = workspace.root / 'nested'
    arguments = dict(path=nested, owner_id='', actor='alice', operation_id='nested-create')
    created = creator.create(**arguments)
    mover = DirectoryMoveStore()
    table = f'"{mover.database.schema}".resource_creations'
    with mover.database.read() as c:
        before = dict(c.execute(f'SELECT * FROM {table} WHERE resource_id=%s',
                               (created['workspace_id'],)).fetchone())
    target = workspace.root.with_name('moved')
    mover.move(workspace.root, target, owner_id=WID, origin='created', actor='alice',
               terminate=lambda _: None, database=workspace.store.database,
               update=relocate_nested, dependencies=lambda *_: None)
    mover.cleanup.run_pending()
    creator.create(path=workspace.root, owner_id='', actor='alice', operation_id='reused-parent')
    creator.create(**{**arguments, 'operation_id': 'new-nested'})
    (nested / 'new-file').write_text('new owner')
    retried = creator.create(**arguments)
    assert retried['workspace_id'] == created['workspace_id']
    assert retried['path'] == str(target / 'nested')
    with mover.database.read() as c:
        after = dict(c.execute(f'SELECT * FROM {table} WHERE resource_id=%s',
                              (created['workspace_id'],)).fetchone())
    assert after == before
    mover.cleanup.run_pending()
    assert (nested / 'new-file').read_text() == 'new owner'


def test_move_does_not_rewrite_completed_cleanup_receipts(workspace):
    mover = DirectoryMoveStore()
    path = workspace.root / 'reused-file'
    path.write_text('old')
    cleanup_id = mover.database.write(lambda c: mover.cleanup.enqueue(path, owner_id='old',
        operation_id='old-cleanup', actor='alice', connection=c))
    mover.cleanup.run_pending()
    before, = mover.cleanup.status(cleanup_id=cleanup_id)
    path.write_text('new')
    target = workspace.root.with_name('moved')
    mover.move(workspace.root, target, owner_id=WID, origin='created', actor='alice',
               terminate=lambda _: None, database=workspace.store.database,
               update=relocate_nested, dependencies=lambda *_: None)
    mover.cleanup.run_pending()
    assert mover.cleanup.status(cleanup_id=cleanup_id) == [before]
    assert (target / path.name).read_text() == 'new'

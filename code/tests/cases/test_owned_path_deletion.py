"""Identity deletion claims preserve foreign entries and survive interrupted owners.

:design: decision_4aa13bc21e8e
"""
import os

import pytest

from management_common import secure_paths as fs
from management_common.directory_paths import remove_path
from management_common.owned_path_deletion import OwnedPathDeletionStore, initialize_owned_deletion_schema
from management_common.resource_lifecycle import lifecycle_scope
from management_portal.test_framework.run_cleanup import finish_run
from tests.cases.test_creation_run_cleanup import open_scope, attempt


def identity(path):
    info = path.lstat()
    return [info.st_dev, info.st_ino]


def make(path, kind, foreign):
    if kind == 'directory':
        path.mkdir()
        (path / 'data').write_text('owned')
    elif kind == 'symlink':
        path.symlink_to(foreign)
    else:
        path.write_text('owned')


@pytest.mark.parametrize('kind', ['file', 'symlink', 'directory'])
def test_replacement_at_atomic_claim_is_restored_with_same_inode(tmp_path, monkeypatch, kind):
    target, outside = tmp_path / 'artifact', tmp_path / 'outside'
    outside.write_text('link target must survive')
    make(target, kind, outside)
    expected = identity(target)
    rename = fs.rename_child_no_replace
    injected = []
    def swap(parent, name, claim, slot):
        if name == target.name and not injected:
            target.rename(tmp_path / 'saved-original')
            make(target, kind, outside)
            injected.append(identity(target))
        return rename(parent, name, claim, slot)
    monkeypatch.setattr(fs, 'rename_child_no_replace', swap)
    with pytest.raises(FileExistsError, match='restored'):
        remove_path(target, identity=expected)
    assert identity(target) == injected[0]
    assert outside.read_text() == 'link target must survive'
    assert identity(tmp_path / 'saved-original') == expected
    # A fresh owner still refuses the foreign entry; no transient memory proof.
    with pytest.raises(FileExistsError):
        OwnedPathDeletionStore().remove(target, expected)
    assert identity(target) == injected[0]


@pytest.mark.parametrize('kind', ['file', 'symlink', 'directory'])
def test_public_replacement_at_private_delete_boundary_is_not_deleted(tmp_path, monkeypatch, kind):
    target, outside = tmp_path / 'artifact', tmp_path / 'outside'
    outside.write_text('outside')
    make(target, kind, outside)
    expected = identity(target)
    unlink = fs.unlink_child
    injected = []
    def swap(parent, name):
        if not injected:
            assert not os.path.lexists(target), 'deletion must first claim the public entry'
            target.write_text('foreign host data')
            injected.append(identity(target))
        return unlink(parent, name)
    monkeypatch.setattr(fs, 'unlink_child', swap)
    remove_path(target, identity=expected)
    assert injected and identity(target) == injected[0]
    assert target.read_text() == 'foreign host data'
    assert outside.read_text() == 'outside'
    assert not list(tmp_path.glob('.tfi-delete-*'))


@pytest.mark.parametrize('boundary', ['claim', 'unlink', 'rmdir'])
def test_creation_interruption_recovers_after_store_reconstruction(tmp_path, monkeypatch, boundary):
    lifecycle, scope = open_scope()
    target = tmp_path / 'artifact'
    with lifecycle_scope(scope, actor='alice'):
        creation = attempt(target)
        with creation.prepare([(target, 'file')]):
            creation.publish(lambda _, stage: stage.write_text('owned'))
    function_name = {'claim': 'rename_child_no_replace', 'unlink': 'unlink_child', 'rmdir': 'rmdir_child'}[boundary]
    real = getattr(fs, function_name)
    def interrupted(*args, **kwargs):
        real(*args, **kwargs)
        raise SystemExit('process interrupted after ' + boundary)
    with monkeypatch.context() as patch:
        patch.setattr(fs, function_name, interrupted)
        with pytest.raises(SystemExit):
            finish_run('run-failed')
    assert not target.exists()
    store = OwnedPathDeletionStore()
    with store.db.read() as conn:
        assert conn.execute(f'SELECT 1 FROM {store.table}').fetchone()
    with creation.db.read() as conn:
        row = conn.execute(f'SELECT * FROM {creation.table}').fetchone()
        assert row['aborted'] and not row['cleanup_complete']
    # finish_run constructs its owners anew, and must inspect a missing source's claim.
    result = finish_run('run-failed')
    assert result['status'] == 'complete', result
    assert lifecycle.scope(scope)['state'] == 'complete'
    assert not list(tmp_path.glob('.tfi-delete-*'))
    with store.db.read() as conn:
        assert not conn.execute(f'SELECT 1 FROM {store.table}').fetchone()


def test_unknown_claim_restore_conflict_is_retained_and_recoverable(tmp_path, monkeypatch):
    target = tmp_path / 'artifact'
    target.write_text('owned')
    expected = identity(target)
    rename = fs.rename_child_no_replace
    injected = []
    def swap(parent, name, claim, slot):
        if name == target.name and not injected:
            target.rename(tmp_path / 'saved-original')
            target.write_text('foreign first')
            injected.append(identity(target))
            rename(parent, name, claim, slot)
            target.write_text('foreign second')
            return
        return rename(parent, name, claim, slot)
    with monkeypatch.context() as patch:
        patch.setattr(fs, 'rename_child_no_replace', swap)
        with pytest.raises(FileExistsError, match='retained.*restore failed'):
            remove_path(target, identity=expected)
    stage, = tmp_path.glob('.tfi-delete-*')
    assert identity(stage / 'entry') == injected[0]
    assert target.read_text() == 'foreign second'
    assert OwnedPathDeletionStore().recover()
    assert identity(stage / 'entry') == injected[0]
    # Operator removes the obstruction without losing either foreign object.
    target.rename(tmp_path / 'second-preserved')
    assert OwnedPathDeletionStore().recover()  # restored, still unknown/pending
    assert identity(target) == injected[0]
    assert not (stage / 'entry').exists()
    target.rename(tmp_path / 'first-preserved')
    assert OwnedPathDeletionStore().recover() == []
    assert not stage.exists()


def test_claim_journal_survives_callers_transaction_rollback(tmp_path, monkeypatch):
    from management_common.portal_database import portal_database
    target = tmp_path / 'artifact'
    target.write_text('owned')
    expected = identity(target)
    rename = fs.rename_child_no_replace
    def interrupted(*args):
        rename(*args)
        raise SystemExit('crash')
    with monkeypatch.context() as patch:
        patch.setattr(fs, 'rename_child_no_replace', interrupted)
        with pytest.raises(SystemExit), portal_database(schema='directory_lifecycle').transaction():
            remove_path(target, identity=expected)
    assert not target.exists()
    assert OwnedPathDeletionStore().recover() == []
    assert not list(tmp_path.glob('.tfi-delete-*'))


def test_unproven_private_directory_is_reported_not_adopted(tmp_path, monkeypatch):
    target = tmp_path / 'artifact'
    target.write_text('owned')
    expected = identity(target)
    sync = fs.sync_directory
    def crash(parent):
        sync(parent)
        raise SystemExit('mkdir committed, inode not recorded')
    with monkeypatch.context() as patch:
        patch.setattr(fs, 'sync_directory', crash)
        with pytest.raises(SystemExit):
            remove_path(target, identity=expected)
    stage, = tmp_path.glob('.tfi-delete-*')
    (stage / 'foreign').write_text('unknown')
    errors = OwnedPathDeletionStore().recover()
    assert errors and 'Unproven' in errors[0] and stage.name in errors[0]
    assert (stage / 'foreign').read_text() == 'unknown'
    assert identity(target) == expected


def test_offline_owner_ddl_is_idempotent():
    store = OwnedPathDeletionStore()
    with store.db.transaction() as conn:
        initialize_owned_deletion_schema(conn, store.db.schema)
        initialize_owned_deletion_schema(conn, store.db.schema)
        columns = {row[0] for row in conn.execute(
            'SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND NOT attisdropped', (store.table,))}
    assert {'location', 'identity', 'parent_identity', 'private_name', 'private_identity', 'finished'} <= columns


def test_creation_file_replacement_at_claim_boundary_is_preserved(tmp_path, monkeypatch):
    # R2's real Creation -> finish_run reproduction, keeping all foreign-data
    # assertions. The old unlink(public-name) boundary is now atomic claim.
    from management_common.resource_lifecycle import LifecycleStore
    from management_portal.resources.creation import Creation
    lifecycle = LifecycleStore()
    scope = lifecycle.open('file-create', actor='alice', run_id='file-run', case_id='file')
    target = tmp_path / 'published-file'
    with lifecycle_scope(scope, actor='alice'):
        creation = Creation('paper_565656565656', actor='alice', payload={}, operation_id='file-create')
        with creation.prepare([(target, 'file')]):
            creation.publish(lambda _, stage: stage.write_text('owned'))
    rename = fs.rename_child_no_replace
    injected = []
    def swap(parent, name, claim, slot):
        if name == target.name and not injected:
            target.rename(tmp_path / 'saved-file')
            target.write_text('foreign host data')
            injected.append(identity(target))
        return rename(parent, name, claim, slot)
    monkeypatch.setattr(fs, 'rename_child_no_replace', swap)
    result = finish_run('file-run')
    assert injected
    assert target.exists(), result
    assert target.read_text() == 'foreign host data'
    assert identity(target) == injected[0]
    assert result['status'] == 'incomplete'
    with creation.db.read() as conn:
        row = conn.execute(f'SELECT * FROM {creation.table}').fetchone()
    assert row['aborted'] and not row['cleanup_complete']
    assert finish_run('file-run')['status'] == 'incomplete'
    assert identity(target) == injected[0]


def test_native_claim_does_not_overwrite_an_existing_slot(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.write_text('source')
    destination.write_text('destination')
    with fs.open_directory(tmp_path) as parent:
        with pytest.raises(FileExistsError):
            fs.rename_child_no_replace(parent, source.name, parent, destination.name)
    assert source.read_text() == 'source'
    assert destination.read_text() == 'destination'


def test_owned_parent_replacement_retains_claim_instead_of_false_completion(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'
    parent.mkdir()
    target = parent / 'artifact'
    target.write_text('owned')
    expected = identity(target)
    rename = fs.rename_child_no_replace
    def crash(*args):
        rename(*args)
        raise SystemExit('claim interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(fs, 'rename_child_no_replace', crash)
        with pytest.raises(SystemExit):
            remove_path(target, identity=expected)
    parent.rename(tmp_path / 'saved-parent')
    parent.mkdir()
    target.write_text('foreign')
    with pytest.raises(FileExistsError, match='parent was replaced'):
        OwnedPathDeletionStore().remove(target, expected)
    assert target.read_text() == 'foreign'
    claimed, = (tmp_path / 'saved-parent').glob('.tfi-delete-*/entry')
    assert identity(claimed) == expected
    target.unlink()
    parent.rmdir()
    (tmp_path / 'saved-parent').rename(parent)
    assert OwnedPathDeletionStore().recover() == []
    assert not target.exists()


def test_unqualified_tree_deletion_has_no_claim_journal(tmp_path, monkeypatch):
    path = tmp_path / 'tree'
    path.mkdir()
    (path / 'one').write_text('one')
    (path / 'two').write_text('two')
    def forbid(*args, **kwargs):
        raise AssertionError('unqualified tree deletion must not allocate inode claims')
    monkeypatch.setattr(OwnedPathDeletionStore, '__init__', forbid)
    remove_path(path)
    assert not path.exists()


@pytest.mark.parametrize('kind', ['file', 'symlink', 'directory'])
def test_abrupt_child_exit_after_native_claim_is_recovered(tmp_path, kind):
    import subprocess
    import sys
    target, outside = tmp_path / 'artifact', tmp_path / 'outside'
    outside.write_text('outside')
    make(target, kind, outside)
    expected = identity(target)
    child = '''
import os, sys
from pathlib import Path
from management_common import secure_paths as fs
from management_common.directory_paths import remove_path
rename = fs.rename_child_no_replace
def crash(*args):
    rename(*args)
    os._exit(73)
fs.rename_child_no_replace = crash
remove_path(Path(sys.argv[1]), identity=[int(sys.argv[2]), int(sys.argv[3])])
'''
    result = subprocess.run([sys.executable, '-c', child, str(target), *map(str, expected)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    assert not os.path.lexists(target)
    claimed, = tmp_path.glob('.tfi-delete-*/entry')
    assert identity(claimed) == expected
    assert OwnedPathDeletionStore().recover() == []
    assert outside.read_text() == 'outside'
    assert not list(tmp_path.glob('.tfi-delete-*'))


def test_private_unlink_failure_is_retryable_with_missing_public_path(tmp_path, monkeypatch):
    target = tmp_path / 'artifact'
    target.write_text('owned')
    expected = identity(target)
    with monkeypatch.context() as patch:
        patch.setattr(fs, 'unlink_child', lambda *args: (_ for _ in ()).throw(PermissionError('busy')))
        with pytest.raises(PermissionError, match='busy'):
            remove_path(target, identity=expected)
    assert not target.exists()
    claimed, = tmp_path.glob('.tfi-delete-*/entry')
    assert identity(claimed) == expected
    OwnedPathDeletionStore().remove(target, expected)
    assert not list(tmp_path.glob('.tfi-delete-*'))

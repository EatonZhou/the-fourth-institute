"""Temporary allocations use isolated PostgreSQL leases, never age guesses."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from management_common import file_retention as files
from management_common.directory_retention import RetentionStore
from management_common.directory_paths import cleanup_location


def declaration(path):
    store = RetentionStore()
    with store.database.read() as conn:
        row = conn.execute(f'SELECT * FROM {store.table} WHERE location=%s',
                           (cleanup_location(path),)).fetchone()
        return dict(row) if row else None


@pytest.mark.parametrize('fail', [False, True])
def test_normal_and_exception_cleanup(tmp_path, fail):
    try:
        with files.temporary_directory(prefix='temp-', dir=tmp_path) as name:
            path = Path(name)
            assert declaration(path)['category'] == 'runtime_tmp'
            (path / 'content').write_text('owned')
            assert files.recover_temporary_directories()['active'] == [str(path)]
            if fail:
                raise RuntimeError('body failed')
    except RuntimeError:
        assert fail
    assert not path.exists() and declaration(path) is None


def test_run_ownership_and_live_sibling(tmp_path):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    run = 'testrun_1234567890abcdef'
    scope = LifecycleStore().open(str(tmp_path), actor='alice', run_id=run, case_id='temporary')
    with lifecycle_scope(scope, actor='alice'):
        with files.temporary_directory(prefix='first-', dir=tmp_path) as first:
            with files.temporary_directory(prefix='second-', dir=tmp_path) as second:
                assert declaration(Path(first))['test_run_id'] == run
                assert declaration(Path(second))['owner_id'] == run
            assert Path(first).exists() and not Path(second).exists()
            assert files.recover_temporary_directories()['active'] == [first]
    assert not Path(first).exists()


def test_busy_delete_persists_retry(tmp_path, monkeypatch):
    from management_common.owned_path_deletion import OwnedPathDeletionStore
    original = OwnedPathDeletionStore.remove
    def busy(*args, **kwargs):
        raise PermissionError('occupied fixture')
    monkeypatch.setattr(OwnedPathDeletionStore, 'remove', busy)
    with files.temporary_directory(prefix='busy-', dir=tmp_path) as name:
        path = Path(name)
    queue = RetentionStore().queue
    assert path.exists() and queue.status()[0]['status'] == 'retry'
    assert declaration(path) is None
    monkeypatch.setattr(OwnedPathDeletionStore, 'remove', original)
    queue.database.write(lambda conn: conn.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp()'))
    report = files.recover_temporary_directories()
    assert not report['errors'] and not path.exists()
    assert queue.status()[0]['status'] == 'completed'


@pytest.mark.parametrize('entry', ['helper', 'store'])
def test_process_death_recovered_by_maintenance_only_for_helper(tmp_path, entry):
    from management_portal import directory_maintenance
    unrelated = tmp_path / 'other-owner'
    unrelated.mkdir()
    RetentionStore().declare(unrelated, category='runtime_tmp', owner_id='other-owner')
    code = '''
from management_common.file_retention import temporary_directory
from pathlib import Path
import sys
if sys.argv[2] == 'helper':
    with temporary_directory(prefix='crashed-', dir=sys.argv[1]) as path:
        print(path, flush=True)
        sys.stdin.read()
else:
    from management_portal.workbench_projections import application_build as build
    build._artifact_root = lambda: Path(sys.argv[1]) / 'builds'
    def interrupted(index, contents, digest, root, temporary):
        (temporary / 'partial').write_text('partial build')
        print(temporary, flush=True)
        sys.stdin.read()
    build._write_artifact_files = interrupted
    build._store({'files': {}}, {})
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(tmp_path), entry], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        name = child.stdout.readline().strip()
        assert name, child.stderr.read()
        path = Path(name)
        # Build publication holds the path transaction lock; recovery still
        # skips its live session without waiting for that transaction.
        assert files.recover_temporary_directories()['active'] == [name]
        assert path.exists()
        child.kill()
        child.wait(timeout=10)
        report = directory_maintenance.run(test_roots=[])
        assert not report['temporary_directories']['errors']
        assert not path.exists() and declaration(path) is None
        assert unrelated.exists() and declaration(unrelated)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_store_files_registers_real_pending_directory(tmp_path, monkeypatch):
    from management_portal.workbench_projections import application_build as build
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    root = tmp_path / 'builds'
    monkeypatch.setattr(build, '_artifact_root', lambda: root)
    run = 'testrun_build_temp'
    scope = LifecycleStore().open(str(root), actor='alice', run_id=run, case_id='build')
    original = build.sync_directory
    observed = []
    def sync(path):
        pending = list(root.glob('.pending-*'))
        if pending:
            row = declaration(pending[0])
            assert row is not None and row['category'] == 'runtime_tmp'
            assert row['test_run_id'] == run and row['owner_id'] == run
            observed.append(pending[0])
        original(path)
    monkeypatch.setattr(build, 'sync_directory', sync)
    with lifecycle_scope(scope, actor='alice'):
        digest = build._store({'files': {}}, {})
    assert observed and (root / digest / 'artifact.json').exists()
    assert all(not path.exists() for path in observed)


def test_store_failure_cleans_declared_staging(tmp_path, monkeypatch):
    from management_portal.workbench_projections import application_build as build
    monkeypatch.setattr(build, '_artifact_root', lambda: tmp_path)
    observed = []
    def fail(path):
        pending = next(tmp_path.glob('.pending-*'))
        assert declaration(pending) is not None
        observed.append(pending)
        raise OSError('write failed')
    monkeypatch.setattr(build, 'sync_directory', fail)
    with pytest.raises(OSError, match='write failed'):
        build._store({'files': {}}, {})
    assert observed and all(not path.exists() for path in observed)


def test_declaration_commits_before_exclusive_creation_and_race_is_not_owned(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from management_common import secure_paths as fs
    from contextlib import contextmanager
    original = fs.private_child
    intruder = []
    @contextmanager
    def race(parent, name, *, create=False):
        path = tmp_path / name
        # A separate connection must observe the declaration before mkdir.
        with ThreadPoolExecutor() as pool:
            assert pool.submit(declaration, path).result()['category'] == 'runtime_tmp'
        path.mkdir()
        (path / 'not-ours').write_text('keep')
        intruder.append(path)
        with original(parent, name, create=create) as fd:
            yield fd
    monkeypatch.setattr(fs, 'private_child', race)
    with pytest.raises(FileExistsError):
        with files.temporary_directory(prefix='collision-', dir=tmp_path):
            pytest.fail('must not yield a colliding entry')
    assert (intruder[0] / 'not-ours').read_text() == 'keep'
    assert declaration(intruder[0]) is None
    assert not RetentionStore().queue.status()
    assert not files.recover_temporary_directories()['cleanup']


def test_preexisting_name_is_not_declared_or_removed(tmp_path, monkeypatch):
    import uuid
    from types import SimpleNamespace
    monkeypatch.setattr(uuid, 'uuid4', lambda: SimpleNamespace(hex='fixed'))
    path = tmp_path / 'existing-fixed'
    path.mkdir()
    (path / 'keep').write_text('existing')
    with pytest.raises(FileExistsError):
        with files.temporary_directory(prefix='existing-', dir=tmp_path):
            pytest.fail('existing directory must not be adopted')
    assert (path / 'keep').exists() and declaration(path) is None
    assert not RetentionStore().queue.status()


@pytest.mark.parametrize('mkdir_completed', [False, True])
def test_crash_at_allocation_boundary_retains_unproven_entry(tmp_path, mkdir_completed):
    code = '''
from management_common import file_retention, secure_paths as fs
import os, sys
if sys.argv[2] == 'True':
    fs.sync_directory = lambda *a: os._exit(81)
else:
    fs.private_child = lambda *a, **k: os._exit(81)
with file_retention.temporary_directory(prefix='boundary-', dir=sys.argv[1]):
    raise AssertionError('unreachable')
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path), str(mkdir_completed)],
                            capture_output=True, timeout=15)
    assert result.returncode == 81, result.stderr
    report = files.recover_temporary_directories()
    paths = list(tmp_path.glob('boundary-*'))
    if mkdir_completed:
        assert len(paths) == 1 and declaration(paths[0])
        assert 'Unproven temporary allocation retained' in report['errors'][0]
        assert not RetentionStore().queue.status()
    else:
        assert not paths and not report['errors']
        store = RetentionStore()
        with store.database.read() as conn:
            assert not conn.execute(f'SELECT * FROM {store.queue.reservations}').fetchall()


def test_replaced_allocation_is_retained_with_retry_receipt(tmp_path):
    with files.temporary_directory(prefix='replace-', dir=tmp_path) as name:
        path = Path(name)
        path.rename(tmp_path / 'moved-original')
        path.mkdir()
        (path / 'keep').write_text('replacement')
    assert (path / 'keep').read_text() == 'replacement'
    receipt = RetentionStore().queue.status()[0]
    assert receipt['status'] == 'retry' and 'ownership changed' in receipt['last_error']


def test_queued_outer_cleanup_cannot_delete_active_temporary(tmp_path):
    store = RetentionStore()
    root = tmp_path / 'outer'
    root.mkdir()
    with files.temporary_directory(prefix='active-', dir=root) as name:
        with store.database.transaction() as conn:
            store.enqueue_cleanup(root, owner_id='outer', operation_id='outer-delete', actor='test', connection=conn)
        assert store.queue.run_pending() == []
        assert Path(name).exists()
        assert files.recover_temporary_directories()['active'] == [name]
    store.queue.run_pending()
    assert not root.exists()


def test_temporary_creation_rejects_uncommitted_outer_retention_transaction(tmp_path):
    with RetentionStore().database.transaction():
        with pytest.raises(RuntimeError, match='precede the owner transaction'):
            with files.temporary_directory(prefix='nested-', dir=tmp_path):
                pytest.fail('predeclaration must commit independently')
    assert not list(tmp_path.glob('nested-*'))

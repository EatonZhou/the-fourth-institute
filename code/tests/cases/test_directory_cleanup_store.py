"""Cleanup survives transactions, process replacement and occupied files.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from management_common import directory_cleanup_store as cleanup
from management_common.instance_manifest import current_instance


@pytest.fixture
def store():
    return cleanup.DirectoryCleanupStore()


def enqueue(store, path, operation='delete-1'):
    with store.database.transaction() as connection:
        return store.enqueue(path, owner_id='resource-1', operation_id=operation, actor='alice', connection=connection)


def test_queue_and_owner_commit_or_rollback_together(store, tmp_path):
    root = tmp_path / 'owned'
    root.mkdir()
    with pytest.raises(RuntimeError):
        with store.database.transaction() as connection:
            store.enqueue(root, owner_id='object', operation_id='failed', actor='alice', connection=connection)
            raise RuntimeError('registration failed')
    assert store.status() == []
    identifier = enqueue(store, root)
    assert root.exists()  # The request never touches files.
    assert enqueue(store, root) == identifier
    replacement_process = cleanup.DirectoryCleanupStore()
    assert replacement_process.run_pending()[0]['status'] == 'completed'
    assert not root.exists()
    assert replacement_process.run_pending() == []


def test_occupied_directory_keeps_reservation_until_retry(store, tmp_path, monkeypatch):
    root = tmp_path / 'owned'
    root.mkdir()
    identifier = enqueue(store, root)
    remove = cleanup.remove_path
    def occupied(_path):
        raise PermissionError('file is open')
    monkeypatch.setattr(cleanup, 'remove_path', occupied)
    outcome, = store.run_pending()
    assert outcome['status'] == 'retry'
    assert outcome['last_error'] == 'file is open'
    assert root.exists()
    for path in (root, root / 'child', tmp_path):
        with store.database.transaction() as connection:
            with pytest.raises(cleanup.DirectoryPendingCleanup):
                store.assert_available(path, connection=connection)
    with store.database.transaction() as connection:
        store.assert_available(tmp_path / 'sibling', connection=connection)
        connection.execute(f"UPDATE {store.table} SET available_at=clock_timestamp() WHERE cleanup_id=%s", (identifier,))
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    assert cleanup.DirectoryCleanupStore().run_pending()[0]['status'] == 'completed'
    with store.database.transaction() as connection:
        store.assert_available(root, connection=connection)


@pytest.mark.parametrize('replacement', [False, True])
def test_symlinks_never_delete_the_target(store, tmp_path, replacement):
    target = tmp_path / 'unrelated'
    target.mkdir()
    (target / 'keep').write_text('user data')
    root = tmp_path / 'owned'
    root.mkdir()
    if not replacement:
        (root / 'link').symlink_to(target, target_is_directory=True)
    enqueue(store, root)
    if replacement:
        root.rmdir()
        root.symlink_to(target, target_is_directory=True)
    assert store.run_pending()[0]['status'] == 'completed'
    assert not root.exists() and not root.is_symlink()
    assert (target / 'keep').read_text() == 'user data'


def test_redirected_parent_fails_without_following_it(store, tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    root = parent / 'owned'
    root.mkdir()
    enqueue(store, root)
    original = tmp_path / 'moved'
    parent.rename(original)
    parent.symlink_to(original, target_is_directory=True)
    result, = store.run_pending()
    assert result['status'] == 'retry'
    assert 'symlink' in result['last_error']
    assert (original / 'owned').exists()


@pytest.mark.parametrize('boundary', ['instance', 'area', 'ancestor'])
def test_boundary_errors_are_durable_and_do_not_delete(store, boundary):
    instance = current_instance()
    path = {'instance': instance.root, 'area': instance.area('projects'), 'ancestor': instance.root.parent}[boundary]
    path.mkdir(parents=True, exist_ok=True)
    enqueue(store, path)
    result, = store.run_pending()
    assert result['status'] == 'retry'
    assert 'root' in result['last_error']
    assert path.exists()


def test_concurrent_registration_waits_for_cleanup_to_commit(store, tmp_path, monkeypatch):
    root = tmp_path / 'owned'
    root.mkdir()
    enqueue(store, root)
    removing, proceed, checking = Event(), Event(), Event()
    remove = cleanup.remove_path
    def paused(path):
        removing.set()
        assert proceed.wait(10)
        remove(path)
    def register():
        checking.set()
        with store.database.transaction() as connection:
            store.assert_available(root, connection=connection)
            root.mkdir()
    monkeypatch.setattr(cleanup, 'remove_path', paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(store.run_pending)
        assert removing.wait(10)
        writer = pool.submit(register)
        assert checking.wait(10)
        assert not writer.done()
        proceed.set()
        worker.result(10)
        writer.result(10)
    assert root.exists()


def test_owned_readonly_directory_and_child_are_removed(store, tmp_path):
    root = tmp_path / 'sealed'
    nested = root / 'nested'
    nested.mkdir(parents=True)
    (nested / 'result').write_text('finished')
    nested.chmod(0o000)
    root.chmod(0o000)
    enqueue(store, root)
    assert store.run_pending()[0]['status'] == 'completed'
    assert not root.exists()
def test_cleanup_reserves_case_aliases(tmp_path, monkeypatch):
    from management_common import directory_paths
    monkeypatch.setattr(directory_paths, '_directory_case_sensitive', lambda _: False)
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    queue = get_directory_cleanup_store()
    with queue.database.transaction() as connection:
        queue.enqueue(tmp_path / 'Reserved', owner_id='owner', operation_id='case-cleanup', actor='actor', connection=connection)
    assert queue.pending_at(tmp_path / 'reserved' / 'child')


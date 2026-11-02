"""Direct upload deletion converges through the registered directory lifecycle.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import errno
import json
import os
import shutil

import pytest

from management_common import upload_retention as uploads
from management_common.directory_retention import RetentionStore


def upload(root, name, *, ready=True, payload=True):
    directory = root / ('upload_' + name)
    uploads.created(directory, upload_id=directory.name,
                    created_at=datetime.now(timezone.utc).isoformat())
    (directory / 'meta.json').write_text(json.dumps({'upload_id': directory.name,
        'ready': ready, 'file_name': 'recording.wav'}))
    if payload:
        (directory / 'recording.wav').write_bytes(b'audio')
    return directory


def registered(root):
    return set(RetentionStore().registered_children(root, categories=('unused_upload', 'object_data')))


def test_direct_deletion_releases_used_and_unused_uploads_and_cleans_sidecars(tmp_path):
    root = tmp_path / 'uploads'
    used = upload(root, 'used', payload=False)
    uploads.consumed(used, owner_id='message_kept')
    (used / 'transcript.json').write_text('{}')
    unused = upload(root, 'unused', payload=False)
    gone = upload(root, 'gone')
    shutil.rmtree(gone)
    pending = upload(root, 'pending', ready=False, payload=False)
    initial = upload(root, 'initial', ready=False, payload=False)
    (initial / 'meta.json').unlink()
    edited = upload(root, 'edited')
    (edited / 'recording.wav').write_bytes(b'changed and longer')
    unknown = root / 'upload_unregistered'
    unknown.mkdir()
    (unknown / 'meta.json').write_text('{"ready":true,"file_name":"missing"}')
    outside = upload(tmp_path / 'other', 'outside', payload=False)
    nested = upload(root / 'nested', 'nested', payload=False)
    other_category = root / 'upload_cache'
    other_category.mkdir()
    RetentionStore().declare(other_category, category='cache', owner_id='cache')

    report = uploads.reconcile_missing(root)
    assert report == {'root_available': True, 'checked': 6, 'released': 3, 'kept': 3,
                      'missing_directories': 1, 'missing_payloads': 2, 'errors': []}
    assert registered(root) == {pending, initial, edited}
    queue = RetentionStore().queue
    assert all(item['status'] == 'completed' for item in queue.run_pending())
    assert not any(path.exists() for path in (used, unused, gone))
    assert all(path.exists() for path in (pending, initial, edited, unknown, outside, nested, other_category))
    assert uploads.reconcile_missing(root)['released'] == 0


@pytest.mark.parametrize('failure', ['missing', 'denied', 'io', 'symlink'])
def test_unavailable_root_never_retires_registered_uploads(tmp_path, monkeypatch, failure):
    root = tmp_path / 'uploads'
    directory = upload(root, 'retained', payload=False)
    original = uploads.open_directory
    if failure in {'missing', 'symlink'}:
        root.rename(tmp_path / 'moved')
        if failure == 'symlink':
            root.symlink_to(tmp_path / 'moved', target_is_directory=True)
    else:
        @contextmanager
        def inaccessible(path, *args, **kwargs):
            if path == root:
                raise OSError(errno.EACCES if failure == 'denied' else errno.EIO, failure)
            with original(path, *args, **kwargs) as descriptor:
                yield descriptor
        monkeypatch.setattr(uploads, 'open_directory', inaccessible)
    report = uploads.reconcile_missing(root)
    assert not report['root_available'] and report['released'] == report['checked'] == 0
    assert bool(report['errors']) == (failure != 'missing')
    assert registered(root) == {directory}
    assert not RetentionStore().queue.status()


@pytest.mark.parametrize('failure', ['payload_io', 'metadata_io', 'malformed', 'filename',
                                    'directory_link', 'metadata_link', 'payload_link'])
def test_invalid_or_unreadable_files_never_mean_deleted(tmp_path, monkeypatch, failure):
    root = tmp_path / 'uploads'
    directory = upload(root, 'retained', payload=False)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'meta.json').write_text('{"ready":true,"file_name":"missing"}')
    original = uploads.stat_child
    original_open = uploads.open_child
    if failure == 'payload_io':
        def broken(descriptor, name):
            if name == 'recording.wav':
                raise OSError(errno.EIO, 'storage unavailable')
            return original(descriptor, name)
        monkeypatch.setattr(uploads, 'stat_child', broken)
    elif failure == 'metadata_io':
        def broken_open(descriptor, name, *args, **kwargs):
            if name == 'meta.json':
                raise PermissionError('metadata denied')
            return original_open(descriptor, name, *args, **kwargs)
        monkeypatch.setattr(uploads, 'open_child', broken_open)
    elif failure == 'malformed':
        (directory / 'meta.json').write_text('{')
    elif failure == 'filename':
        (directory / 'meta.json').write_text('{"ready":true,"file_name":"../outside/missing"}')
    elif failure == 'directory_link':
        shutil.rmtree(directory)
        directory.symlink_to(outside, target_is_directory=True)
    elif failure == 'metadata_link':
        (directory / 'meta.json').unlink()
        (directory / 'meta.json').symlink_to(outside / 'meta.json')
    elif failure == 'payload_link':
        (directory / 'recording.wav').symlink_to(outside / 'missing')
    report = uploads.reconcile_missing(root)
    assert report['released'] == 0 and registered(root) == {directory}
    assert not RetentionStore().queue.status()
    assert (outside / 'meta.json').read_text() == '{"ready":true,"file_name":"missing"}'
    assert bool(report['errors']) == (failure != 'payload_link')


@pytest.mark.parametrize('change', ['metadata_replace', 'metadata_gap', 'payload_return', 'root_replace'])
def test_changes_during_absence_check_are_deferred(tmp_path, monkeypatch, change):
    root = tmp_path / 'uploads'
    directory = upload(root, 'changing', payload=False)
    original = uploads.stat_child
    changed = False
    def replace_on_payload_check(descriptor, name):
        nonlocal changed
        if name == 'recording.wav' and not changed:
            changed = True
            if change == 'metadata_replace':
                temporary = directory / '.meta.next'
                temporary.write_text('{"ready":false,"file_name":"recording.wav"}')
                os.replace(temporary, directory / 'meta.json')
            elif change == 'metadata_gap':
                (directory / 'meta.json').rename(directory / '.meta.inprogress')
            elif change == 'payload_return':
                (directory / 'recording.wav').write_bytes(b'new content')
            else:
                root.rename(tmp_path / 'old-root')
                root.mkdir()
            raise FileNotFoundError(name)
        return original(descriptor, name)
    monkeypatch.setattr(uploads, 'stat_child', replace_on_payload_check)
    report = uploads.reconcile_missing(root)
    assert changed and report['released'] == 0
    assert registered(root) == {directory} and not RetentionStore().queue.status()


def test_upload_directory_exists_before_registration_is_committed(tmp_path, monkeypatch):
    root = tmp_path / 'uploads'
    root.mkdir()
    directory = root / 'upload_starting'
    declare = RetentionStore.declare
    observations = []
    def observe_uncommitted(self, *args, **kwargs):
        declare(self, *args, **kwargs)
        # A maintenance runner has its own transaction; nested reads on this
        # thread deliberately reuse the writer's transaction in Portal.
        with ThreadPoolExecutor(max_workers=1) as executor:
            observations.append(executor.submit(uploads.reconcile_missing, root).result(timeout=5))
    monkeypatch.setattr(RetentionStore, 'declare', observe_uncommitted)
    uploads.created(directory, upload_id=directory.name,
                    created_at=datetime.now(timezone.utc).isoformat())
    assert observations[0]['checked'] == 0
    assert directory.is_dir() and registered(root) == {directory}
    assert uploads.reconcile_missing(root)['released'] == 0


def test_reserved_upload_is_not_retired_during_move(tmp_path):
    root = tmp_path / 'uploads'
    directory = upload(root, 'moving', payload=False)
    store = RetentionStore()
    with store.database.transaction() as connection:
        store.queue.reserve(directory, operation_id='move', purpose='move', connection=connection)
    shutil.rmtree(directory)
    report = uploads.reconcile_missing(root)
    assert report['kept'] == 1 and not report['errors']
    assert registered(root) == {directory} and not store.queue.status()


def test_instance_upload_area_and_cleanup_failure_preserve_transaction(monkeypatch):
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    from management_common.portal_paths import path
    root = path('uploads')  # The isolated test instance, stored as uploads:<id>.
    directory = upload(root, 'rollback', payload=False)
    enqueue = DirectoryCleanupStore.enqueue
    def unavailable(*args, **kwargs):
        raise OSError(errno.EIO, 'cleanup unavailable')
    monkeypatch.setattr(DirectoryCleanupStore, 'enqueue', unavailable)
    report = uploads.reconcile_missing(root)
    assert report['released'] == 0 and report['errors']
    assert registered(root) == {directory} and directory.exists()
    assert not RetentionStore().queue.status()
    monkeypatch.setattr(DirectoryCleanupStore, 'enqueue', enqueue)
    assert uploads.reconcile_missing(root)['released'] == 1
    assert registered(root) == set()
    RetentionStore().queue.run_pending()
    assert not directory.exists()


def test_directory_maintenance_converges_direct_deletion(tmp_path, monkeypatch):
    from management_common import portal_paths
    from management_portal.directory_maintenance import run
    root = tmp_path / 'uploads'
    directory = upload(root, 'maintenance', payload=False)
    resolve = portal_paths.path
    monkeypatch.setattr(portal_paths, 'path', lambda area, *args, **kwargs:
                        root if area == 'uploads' else resolve(area, *args, **kwargs))
    report = run(test_roots=[])
    assert report['uploads']['released'] == 1 and not report['uploads']['errors']
    assert report['ok'] and not directory.exists()
    assert registered(root) == set()

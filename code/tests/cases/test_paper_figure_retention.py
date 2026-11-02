"""Paper figure cleanup uses isolated PostgreSQL and actual bundle files.

:design: decision_4aa13bc21e8e
"""
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from management_common import directory_cleanup_store as cleanup
from management_common.directory_paths import cleanup_path
from management_common.directory_retention import RetentionStore, utc_now
from management_portal import paper_figures
from tests.cases.test_paper_figures_audit_regressions import extraction  # noqa: F401


def declarations():
    store = RetentionStore()
    with store.database.read() as connection:
        return connection.execute(f'SELECT * FROM {store.table}').fetchall()


def drain():
    queue = cleanup.DirectoryCleanupStore()
    with queue.database.transaction() as connection:
        connection.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp()')
    return queue.run_pending()


@pytest.mark.parametrize('stage', ['render', 'metadata'])
def test_failed_candidate_has_durable_cleanup_retry(extraction, monkeypatch, stage):
    x = extraction
    old = x.path.read_bytes(), x.image.read_bytes()
    remove = cleanup.remove_path

    def busy(path):
        raise PermissionError('figure fixture occupied')

    def fail(*args):
        raise RuntimeError('before metadata commit')

    monkeypatch.setattr(cleanup, 'remove_path', busy)
    monkeypatch.setattr(paper_figures, '_render_extracted_figures' if stage == 'render' else '_write_json', fail)
    with pytest.raises(RuntimeError, match='before metadata commit'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    pending = [r for r in cleanup.DirectoryCleanupStore().status() if r['status'] == 'retry']
    assert pending and all('occupied' in r['last_error'] for r in pending)
    assert all(cleanup_path(r['location']).exists() for r in pending)
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    assert all(r['status'] == 'completed' for r in drain())
    assert not list((x.directory / 'extracted').glob('run-*'))
    assert (x.path.read_bytes(), x.image.read_bytes()) == old


def test_replaced_image_cleanup_retries_without_expiring_research(extraction, monkeypatch):
    x = extraction
    remove = cleanup.remove_path

    def busy(path):
        if path == x.image:
            raise PermissionError('old figure occupied')
        return remove(path)

    monkeypatch.setattr(cleanup, 'remove_path', busy)
    result = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert x.image.exists()
    assert any(r['status'] == 'retry' and cleanup_path(r['location']) == x.image
               for r in cleanup.DirectoryCleanupStore().status())
    rows = declarations()
    assert rows and all(r['category'] == 'object_data' and r['eligible_at'] is None for r in rows)
    store = RetentionStore()
    assert store.sweep(now=utc_now() + timedelta(days=3650))['queued'] == 0
    assert next(f for f in result['figures'] if f['label'] == 'Figure 2')['explanation'] == x.prior['explanation']
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    drain()
    assert not x.image.exists()
    assert all((x.root / f['image_rel_path']).exists() for f in result['figures'])


def test_discarded_duplicate_in_protected_generation_has_durable_retry(extraction, monkeypatch):
    x = extraction
    x.captions[1].update(x.captions[0])
    remove = cleanup.remove_path
    occupied = []

    def busy(path):
        if path.name.startswith('fig-') and path.parent.name.startswith('run-'):
            occupied.append(path)
            raise PermissionError('duplicate candidate occupied')
        return remove(path)

    monkeypatch.setattr(cleanup, 'remove_path', busy)
    result = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    assert len(result['figures']) == 1
    assert occupied and all(path.exists() for path in occupied)
    assert any(row['status'] == 'retry' and 'duplicate candidate' in row['last_error']
               for row in cleanup.DirectoryCleanupStore().status())
    kept = [x.root / figure['image_rel_path'] for figure in result['figures']]
    assert all(path.exists() and path not in occupied for path in kept)
    monkeypatch.setattr(cleanup, 'remove_path', remove)
    drain()
    assert all(not path.exists() for path in occupied)
    assert all(path.exists() for path in kept)


@pytest.mark.parametrize('error_type', [OSError, FileNotFoundError, ValueError])
def test_unknown_commit_remains_registered_and_protected(extraction, monkeypatch, error_type):
    x = extraction
    read = Path.read_text
    uncertain = False

    def notify(*args, **kwargs):
        nonlocal uncertain
        uncertain = True
        raise RuntimeError('commit receipt lost')

    def unavailable(path, *args, **kwargs):
        if path == x.path and uncertain:
            raise error_type('metadata temporarily unavailable')
        return read(path, *args, **kwargs)

    monkeypatch.setattr(paper_figures, '_notify_paper_projection', notify)
    monkeypatch.setattr(Path, 'read_text', unavailable)
    with pytest.raises(error_type, match='metadata temporarily unavailable'):
        paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    persisted = json.loads(read(x.path))
    assert declarations(), 'uncertain generation must remain recoverable'
    report = paper_figures.recover_figure_artifacts()
    assert report['errors'] and not report['queued']
    assert x.image.exists() and all((x.root / f['image_rel_path']).exists() for f in persisted)
    monkeypatch.setattr(Path, 'read_text', read)
    report = paper_figures.recover_figure_artifacts()
    drain()
    assert not report['errors'] and report['protected']
    assert all((x.root / f['image_rel_path']).exists() for f in persisted)


def test_generated_reference_protects_entire_old_generation(extraction):
    x = extraction
    first = paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')['figures']
    old_path = x.root / first[0]['image_rel_path']
    sibling = old_path.parent / 'keep-with-generation.txt'
    sibling.write_text('belongs to referenced generation')
    first[1]['generated'] = [{'generated_id': 'kept', 'image_rel_path': first[0]['image_rel_path']}]
    x.path.write_text(json.dumps(first))
    paper_figures.extract_figures({'paper_id': x.paper['id']}, 'alice')
    report = paper_figures.recover_figure_artifacts()
    drain()
    assert not report['errors'] and report['protected']
    assert old_path.exists() and sibling.exists()


@pytest.mark.parametrize('phase', ['before_metadata', 'first_extraction', 'after_metadata', 'lost_database_lease'])
def test_live_writer_and_killed_process_recovery(extraction, phase):
    x = extraction
    ready = x.root / 'writer-ready'
    if phase == 'first_extraction':
        x.path.unlink()
    # A separate interpreter holds a real PostgreSQL session lease. SIGKILL
    # deliberately bypasses Python finally/context-manager cleanup.
    script = '''
import json, sys, time
from pathlib import Path
from management_portal import paper_figures as f
root, phase, ready = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
paper = {'id': 'paper_0123456789ab', '_store_root': str(root)}
f._paper_by_id = lambda identifier: paper
f._local_pdf_for_paper = lambda paper: root / 'paper.pdf'
def stop():
    ready.write_text('ready')
    while True: time.sleep(.05)
def render(pdf, generation, paper_root, paper_id, actor):
    path = generation / 'fig.png'
    path.write_bytes(b'real candidate file')
    if phase in {'before_metadata', 'first_extraction', 'lost_database_lease'}: stop()
    return [{'image_rel_path': path.relative_to(root).as_posix(), 'created_by': actor, 'created_at': 1}]
f._render_extracted_figures = render
f._notify_paper_projection = lambda *args, **kwargs: stop()
f.extract_figures({'paper_id': paper['id']}, 'alice')
'''
    with subprocess.Popen([sys.executable, '-c', script, str(x.root), phase, str(ready)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert ready.exists(), 'child writer failed to reach its barrier'
            assert declarations(), 'writer must register before generating files'
            generations = list((x.directory / 'extracted').glob('run-*'))
            assert generations and (generations[0] / 'fig.png').exists()
            if phase == 'lost_database_lease':
                from hashlib import sha256
                database = RetentionStore().database
                identity = database.schema + ':paper-figures:' + x.paper['id']
                key = int.from_bytes(sha256(identity.encode()).digest()[:8], 'big')
                with database.read() as connection:
                    sessions = connection.execute(
                        "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=%s "
                        "AND objid=%s AND objsubid=1 AND granted", (key >> 32, key & 0xffffffff)).fetchall()
                    assert len(sessions) == 1
                    assert connection.execute('SELECT pg_terminate_backend(%s)', (sessions[0]['pid'],)).fetchone()[0]
                assert process.poll() is None, 'writer itself must remain alive'
            report = paper_figures.recover_figure_artifacts()
            assert report['active'] and not report['queued']
            assert all(path.exists() for path in generations)
        finally:
            if process.poll() is None:
                process.kill()
            stdout, stderr = process.communicate(timeout=10)
        assert not stderr, (stdout, stderr)
    report = paper_figures.recover_figure_artifacts()
    assert not report['active'] and not report['errors']
    drain()
    if phase in {'before_metadata', 'first_extraction', 'lost_database_lease'}:
        assert not any(path.exists() for path in generations)
        assert x.image.exists()
    else:
        assert all(path.exists() for path in generations)
        assert all((x.root / f['image_rel_path']).exists() for f in json.loads(x.path.read_text()))
        assert not x.image.exists(), 'old-image intent must survive death after metadata commit'

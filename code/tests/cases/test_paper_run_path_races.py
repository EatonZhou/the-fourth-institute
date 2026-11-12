"""Run-record filesystem races; synthetic bundles, no models or real papers.

:design: decision_528bf9985705, decision_4aa13bc21e8e
"""
import json
import os
from pathlib import Path
import shutil

import pytest

from management_portal import paper_store

PAPER = 'paper_0123456789ab'


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'bundle'
    root.mkdir()
    store = paper_store.PaperStore(tmp_path / 'library')
    monkeypatch.setattr(store, 'manifest_for_paper_id', lambda _: (root / 'manifest.json', {'paper_id': PAPER}))
    monkeypatch.setattr(store, 'paper_dir_from_manifest', lambda *_: root)
    monkeypatch.setattr(paper_store, 'DATA_ROOT', tmp_path / 'data')
    return store, root


def job():
    return {'paper_id': PAPER, 'job_id': 'assets-fixture', 'kind': 'assets', 'status': 'done'}


def test_late_run_write_does_not_recreate_removed_bundle(bundle, monkeypatch):
    store, root = bundle
    def resolve(*_):
        shutil.rmtree(root)
        return root
    monkeypatch.setattr(store, 'paper_dir_from_manifest', resolve)
    try:
        store.write_run_record(job())
    except FileNotFoundError:
        pass
    assert not root.exists(), 'late run writer recreated the removed Paper bundle'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlink substitution')
def test_run_directory_symlink_cannot_redirect_write(bundle, tmp_path):
    store, root = bundle
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (root / 'runs').symlink_to(foreign, target_is_directory=True)
    try:
        store.write_run_record(job())
    except (ValueError, OSError):
        pass
    assert list(foreign.iterdir()) == [], 'run record escaped through a replaced runs directory'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX allows renaming a pinned directory')
def test_run_parent_replacement_during_serialization_is_not_written(bundle, tmp_path, monkeypatch):
    store, root = bundle
    moved = tmp_path / 'moved'
    original = json.dump
    replaced = False
    def replace_path(value, *args, **kwargs):
        nonlocal replaced
        if isinstance(value, dict) and value.get('job_id') == 'assets-fixture' and not replaced:
            replaced = True
            root.rename(moved)
            root.symlink_to(moved, target_is_directory=True)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'dump', replace_path)
    try:
        store.write_run_record(job())
    except (ValueError, OSError):
        pass
    assert replaced
    assert not list(moved.rglob('*.json')), 'changed parent still published the run'
    assert not list(moved.rglob('.paper-run-*')), 'temporary run was not cleaned'


def test_normal_run_write_preserves_mode_and_other_files(bundle):
    store, root = bundle
    (root / 'paper.pdf').write_bytes(b'%PDF synthetic untouched')
    store.write_run_record(job())
    target = root / 'runs/assets-fixture.json'
    target.chmod(0o640)
    store.write_run_record({**job(), 'status': 'error'})
    assert json.loads(target.read_text())['status'] == 'error'
    if os.name != 'nt':
        assert target.stat().st_mode & 0o777 == 0o640
    assert (root / 'paper.pdf').read_bytes() == b'%PDF synthetic untouched'
    assert sorted(p.name for p in target.parent.iterdir()) == ['assets-fixture.json']


def test_deleted_identity_does_not_create_run_directory(bundle, monkeypatch):
    store, root = bundle
    monkeypatch.setattr(store, 'manifest_for_paper_id', lambda _: None)
    store.write_run_record(job())
    assert not (root / 'runs').exists()


def test_history_tolerates_run_removed_before_stat(bundle, monkeypatch):
    store, root = bundle
    store.write_run_record(job())
    target = root / 'runs/assets-fixture.json'
    keep = root / 'runs/keep.json'
    keep.write_text(json.dumps({**job(), 'job_id': 'keep'}))
    monkeypatch.setattr(paper_store, 'PAPER_STORE', store)
    original = Path.stat
    removed = False
    def stat(path, *args, **kwargs):
        nonlocal removed
        if path == target and not removed:
            removed = True
            path.unlink()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', stat)
    rows = paper_store._paper_run_records(PAPER, kind='assets')
    assert removed
    assert [row['job_id'] for row in rows] == ['keep']

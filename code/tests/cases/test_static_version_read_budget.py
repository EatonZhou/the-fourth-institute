"""Retained static assets renew without a whole-cache scan or unbounded reads.

:module: platform.core
:design: decision_f25266e5a0e7
:importance: P1
"""
import hashlib
import os
import time
from pathlib import Path

from management_portal import http_static_versions as versions


def test_renewal_does_not_rescan_all_retained_assets(tmp_path, monkeypatch):
    directory = tmp_path / 'versions'
    directory.mkdir()
    monkeypatch.setattr(versions, '_directory', lambda: directory)
    data = b'unchanged script'
    version = 'sha256-' + hashlib.sha256(data).hexdigest()
    assets = [tmp_path / f'{i}.js' for i in range(128)]
    entries = [versions._entry(path, version) for path in assets]
    old = time.time() - 120
    for entry in entries:
        entry.write_bytes(data)
        os.utime(entry, (old, old))
    original_glob = Path.glob
    scans = []

    def glob(path, pattern, *args, **kwargs):
        if path == directory:
            scans.append(pattern)
        return original_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, 'glob', glob)
    for asset in assets:
        versions.remember(asset, version, data)
    assert scans == [], f'unchanged assets caused {len(scans)} whole-cache scans'
    assert all(entry.stat().st_mtime > old for entry in entries)
    assert all(versions.recall(asset, version) == data for asset in assets)
    # Admissions still enforce capacity, even after a renewal-only workload.
    monkeypatch.setattr(versions, 'MAX_FILES', 128)
    versions.remember(tmp_path / 'new.js', version, data)
    assert scans == ['*.asset']
    assert len(list(original_glob(directory, '*.asset'))) == 128


def test_recall_bounds_actual_read_even_when_cache_file_grows(tmp_path, monkeypatch):
    monkeypatch.setattr(versions, '_directory', lambda: tmp_path)
    monkeypatch.setattr(versions, 'MAX_BYTES', 6)
    data = b'abc'
    version = 'sha256-' + hashlib.sha256(data).hexdigest()
    asset = tmp_path / 'script.js'
    entry = versions._entry(asset, version)
    entry.write_bytes(data)
    original_open = Path.open
    reads = []

    class Reader:
        def __enter__(self):
            # Simulate replacement/growth after all metadata checks.
            with original_open(entry, 'wb') as stream:
                stream.write(b'x' * 8192)
            self.stream = original_open(entry, 'rb')
            return self

        def read(self, size=-1):
            reads.append(size)
            return self.stream.read(size)

        def __exit__(self, *args):
            self.stream.close()

    def open_file(path, mode='r', *args, **kwargs):
        if path == entry and mode == 'rb':
            return Reader()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_file)
    assert versions.recall(asset, version) is None
    assert reads == [7]

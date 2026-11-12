"""Backup projections enforce their byte budget on the opened document.

:module: operations.runtime
:design: decision_b06ae321a668
:importance: P1
"""
import io
from pathlib import Path

import pytest

from management_portal import resource_backup_backend as backend


@pytest.mark.parametrize('kind', ['json', 'yaml'])
def test_replaced_document_cannot_bypass_size_check(tmp_path, monkeypatch, kind):
    path = tmp_path / ('status.' + kind)
    path.write_text('{}')
    read_sizes = []
    bodies = []
    original = Path.open

    class ObservedBytes(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(size)

    class ObservedText(io.StringIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(size)

    def replaced(self, mode='r', *args, **kwargs):
        if self != path:
            return original(self, mode, *args, **kwargs)
        # The producer replaces the previously small document after stat.
        text = '{}' + ' ' * 8192
        body = ObservedBytes(text.encode()) if 'b' in mode else ObservedText(text)
        bodies.append(body)
        return body

    monkeypatch.setattr(backend, '_MAX_DOCUMENT_BYTES', 16)
    monkeypatch.setattr(Path, 'open', replaced)
    load = getattr(backend.ResourceBackupBackend, '_load_' + kind)
    assert load(path) == ({}, 'invalid')
    assert read_sizes == [17]
    assert all(body.closed for body in bodies)


@pytest.mark.parametrize('kind', ['json', 'yaml'])
def test_document_boundary_and_failure_states(tmp_path, monkeypatch, kind):
    path = tmp_path / ('status.' + kind)
    load = getattr(backend.ResourceBackupBackend, '_load_' + kind)
    monkeypatch.setattr(backend, '_MAX_DOCUMENT_BYTES', 16)
    assert load(path) == ({}, 'missing')
    path.write_bytes(b'{"a":1}' + b' ' * 9)
    assert load(path) == ({'a': 1}, 'ok')
    path.write_bytes(b'{"a":1}' + b' ' * 10)
    assert load(path) == ({}, 'invalid')
    path.write_bytes(b'\xff')
    assert load(path) == ({}, 'invalid')
    path.write_text('[]')
    assert load(path) == ({}, 'invalid')
    path.write_text('{invalid')
    assert load(path) == ({}, 'invalid')

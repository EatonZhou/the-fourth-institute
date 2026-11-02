"""Prepared resource contents are durably flushed before publication."""
import errno
import os
from types import SimpleNamespace

import pytest

from management_portal.resources import creation


@pytest.mark.parametrize('directory', [False, True])
def test_prepared_contents_flush_on_windows(tmp_path, monkeypatch, directory):
    root = tmp_path / 'stage'
    if directory:
        (root / 'nested').mkdir(parents=True)
        first = root / 'one.txt'
        second = root / 'nested/two.txt'
        second.write_bytes(b'second prepared content')
        expected = {first, second}
    else:
        first = root
        expected = {first}
    first.write_bytes(b'first prepared content')
    opened, flushed, closed = {}, set(), set()

    def native_open(path, flags):
        fd = os.open(path, flags)
        opened[fd] = (path, flags)
        return fd

    def native_flush(fd):
        path, flags = opened[fd]
        if not flags & (os.O_RDWR | os.O_WRONLY):
            raise OSError(errno.EBADF, 'Windows fsync requires writable access')
        os.fsync(fd)
        flushed.add(path)

    def native_close(fd):
        closed.add(opened[fd][0])
        os.close(fd)

    monkeypatch.setattr(creation, 'os', SimpleNamespace(
        name='nt', O_RDONLY=os.O_RDONLY, O_RDWR=os.O_RDWR,
        open=native_open, fsync=native_flush, close=native_close))
    creation._sync(root)
    assert flushed == closed == expected
    assert first.read_bytes() == b'first prepared content'


def test_native_prepared_file_can_be_synced(tmp_path):
    sample = tmp_path / 'prepared.txt'
    sample.write_bytes(b'prepared resource')
    creation._sync(sample)
    assert sample.read_bytes() == b'prepared resource'


def test_flush_failure_is_not_hidden_and_closes_descriptor(tmp_path, monkeypatch):
    sample = tmp_path / 'prepared.txt'
    sample.write_bytes(b'prepared resource')
    descriptors = []
    closed = []
    original_open, original_close = os.open, os.close

    def open_file(path, flags):
        descriptor = original_open(path, flags)
        descriptors.append(descriptor)
        return descriptor

    def fail_flush(descriptor):
        raise OSError(errno.EIO, 'native flush failed')

    def close_file(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(creation, 'os', SimpleNamespace(
        name='nt', O_RDONLY=os.O_RDONLY, O_RDWR=os.O_RDWR,
        open=open_file, fsync=fail_flush, close=close_file))
    with pytest.raises(OSError) as error:
        creation._sync(sample)
    assert error.value.errno == errno.EIO
    assert closed == descriptors and len(descriptors) == 1

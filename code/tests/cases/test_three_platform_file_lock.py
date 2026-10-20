"""Shared lock behavior: real process exclusion plus Windows CRT contract probes."""
import errno
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from management_common import file_lock


@pytest.fixture
def windows(monkeypatch):
    calls = []
    crt = SimpleNamespace(LK_NBLCK=2, LK_LOCK=1, LK_UNLCK=0)
    crt.locking = lambda fd, mode, size: calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), size))
    monkeypatch.setitem(sys.modules, "msvcrt", crt)
    # Replace only this module's os view, not pathlib/pytest's platform.
    monkeypatch.setattr(file_lock, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    return crt, calls


@pytest.mark.parametrize("payload", [b"", b"original timestamp\n"])
def test_windows_lock_does_not_change_content_or_position(tmp_path, windows, payload):
    path = tmp_path / "state"
    path.write_bytes(payload)
    with path.open("r+b") as stream:
        stream.seek(len(payload))
        assert file_lock.try_lock_descriptor(stream.fileno())
        assert stream.tell() == len(payload)
        assert path.read_bytes() == payload
        file_lock.unlock_descriptor(stream.fileno())
        assert stream.tell() == len(payload)
        with file_lock.lock_stream(stream):
            assert stream.tell() == len(payload)
            assert path.read_bytes() == payload
    assert all(offset == 0 and size == 1 for _, offset, size in windows[1])


def test_windows_blocking_waits_past_crt_retry_window(tmp_path, windows, monkeypatch):
    crt, calls = windows
    remaining = [12]
    def locking(fd, mode, size):
        calls.append(mode)
        if mode == crt.LK_LOCK:
            raise OSError(errno.EDEADLK, "CRT timed out")
        if mode == crt.LK_NBLCK and remaining[0]:
            remaining[0] -= 1
            raise OSError(errno.EACCES, "contended")
    crt.locking = locking
    import time
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with (tmp_path / "lock").open("a+b") as stream:
        with pytest.raises(ValueError, match="body"):
            with file_lock.lock_stream(stream):
                raise ValueError("body")
    assert remaining == [0]
    assert calls[-1] == crt.LK_UNLCK


def test_windows_errors_restore_position_and_do_not_enter_body(tmp_path, windows):
    crt, _ = windows
    def invalid(fd, mode, size):
        raise OSError(errno.EBADF, "bad descriptor")
    crt.locking = invalid
    with (tmp_path / "lock").open("w+b") as stream:
        stream.write(b"payload")
        stream.flush()
        with pytest.raises(OSError) as caught:
            with file_lock.lock_stream(stream):
                pytest.fail("entered without acquiring")
        assert caught.value.errno == errno.EBADF
        assert stream.tell() == 7


def test_real_process_lock_release_and_exception(tmp_path):
    path = tmp_path / "lock"
    path.write_bytes(b"unchanged")
    probe = "from management_common.file_lock import try_lock_descriptor; import sys; f=open(sys.argv[1],'r+b'); print(try_lock_descriptor(f.fileno()))"
    def contender():
        return subprocess.check_output([sys.executable, "-c", probe, str(path)], text=True).strip()
    with path.open("r+b") as stream:
        with pytest.raises(RuntimeError):
            with file_lock.lock_stream(stream):
                assert contender() == "False"
                raise RuntimeError("release on error")
        assert contender() == "True"
        assert file_lock.try_lock_descriptor(stream.fileno())
        assert contender() == "False"
        file_lock.unlock_descriptor(stream.fileno())
        assert contender() == "True"
    assert path.read_bytes() == b"unchanged"

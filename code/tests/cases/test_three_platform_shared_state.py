"""State writes remain atomic and exclusive without POSIX directory fsync."""
import errno
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import shared_state
from management_common import file_durability


@pytest.fixture
def case_dir(tmp_path):
    # The autouse site fixture registers accounts under tmp_path/accounts and,
    # since 2ae2514, leaves registration locks there; inspect only this case's files.
    path = tmp_path / "case"
    path.mkdir()
    return path


@pytest.fixture
def windows_io(monkeypatch):
    values = {key: value for key, value in vars(os).items() if key not in {"fchmod", "O_DIRECTORY"}}
    def open_file(path, flags, *args, **kwargs):
        if Path(path).is_dir():
            raise PermissionError(errno.EACCES, "Windows CRT cannot open directory")
        return os.open(path, flags, *args, **kwargs)
    values.update(name="nt", open=open_file)
    monkeypatch.setattr(shared_state, "os", SimpleNamespace(**values))
    monkeypatch.setattr(file_durability, "os", SimpleNamespace(**values))
    def prepare(temporary, target, mode):
        assert Path(temporary).read_bytes() == b""
        os.chmod(temporary, mode)
    def created(path, mode):
        assert Path(path).read_bytes() == b""
        os.chmod(path, mode)
    monkeypatch.setitem(sys.modules, "management_common.file_permissions", SimpleNamespace(
        prepare_atomic_permissions=prepare, set_created_file_permissions=created))


def test_windows_atomic_json_commits_without_opening_directory(case_dir, windows_io):
    target = case_dir / "state.json"
    shared_state.atomic_replace_json(target, {"text": "中文", "revision": 1})
    assert json.loads(target.read_text()) == {"text": "中文", "revision": 1}
    assert list(case_dir.iterdir()) == [target]


def test_windows_create_stays_exclusive_without_fchmod(tmp_path, windows_io):
    target = tmp_path / "identity.json"
    shared_state.atomic_create_json(target, {"id": "fixed"})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        shared_state.atomic_create_json(target, {"id": "different"})
    assert target.read_bytes() == before


def test_replace_failure_keeps_original_and_cleans_temporary(case_dir, monkeypatch):
    target = case_dir / "state.json"
    target.write_bytes(b'{"old":true}')
    def blocked(*args, **kwargs):
        raise PermissionError("sharing violation")
    monkeypatch.setattr(shared_state.os, "replace", blocked)
    with pytest.raises(PermissionError):
        shared_state.atomic_replace_json(target, {"new": True})
    assert target.read_bytes() == b'{"old":true}'
    assert list(case_dir.iterdir()) == [target]


def test_windows_permission_failure_never_writes_secret(case_dir, windows_io, monkeypatch):
    target = case_dir / "secret.json"
    target.write_bytes(b"old")
    def denied(temporary, target, mode):
        assert Path(temporary).read_bytes() == b""
        raise PermissionError("ACL unavailable")
    monkeypatch.setattr(sys.modules["management_common.file_permissions"], "prepare_atomic_permissions", denied)
    with pytest.raises(PermissionError, match="ACL unavailable"):
        shared_state.atomic_replace_json(target, {"secret": "never written"}, mode=0o600)
    assert target.read_bytes() == b"old"
    assert list(case_dir.iterdir()) == [target]


@pytest.mark.parametrize('kind', ['json', 'text'])
@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_windows_atomic_replace_waits_for_short_reader(case_dir, windows_io, monkeypatch, kind, winerror):
    target = case_dir / 'state.json'
    target.write_text('old')
    attempts = []
    sleeps = []
    def replace(source, destination):
        attempts.append(source)
        if len(attempts) == 1:
            assert target.read_text() == 'old'
            error = PermissionError('reader holds target')
            error.winerror = winerror
            raise error
        os.replace(source, destination)
    monkeypatch.setattr(shared_state.os, 'replace', replace)
    monkeypatch.setattr(shared_state.time, 'sleep', sleeps.append)
    if kind == 'json':
        shared_state.atomic_replace_json(target, {'revision': 2})
        assert json.loads(target.read_text()) == {'revision': 2}
    else:
        shared_state.atomic_replace_text(target, 'new')
        assert target.read_text() == 'new'
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert sleeps == [0.02]
    assert list(case_dir.iterdir()) == [target]


def test_windows_permanent_replace_denial_is_bounded_and_keeps_original(case_dir, windows_io, monkeypatch):
    target = case_dir / 'state.json'
    target.write_text('old')
    clock = iter([0, 0, 1.01])
    monkeypatch.setattr(shared_state.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(shared_state.time, 'sleep', lambda _: None)
    error = PermissionError('still denied')
    error.winerror = 5
    def denied(*_):
        raise error
    monkeypatch.setattr(shared_state.os, 'replace', denied)
    with pytest.raises(PermissionError) as caught:
        shared_state.atomic_replace_json(target, {'revision': 2})
    assert caught.value is error
    assert target.read_text() == 'old'
    assert list(case_dir.iterdir()) == [target]

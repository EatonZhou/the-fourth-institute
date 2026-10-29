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

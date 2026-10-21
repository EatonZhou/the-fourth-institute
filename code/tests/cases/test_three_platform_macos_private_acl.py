"""macOS private files must not treat mode bits as an ACL check."""
import ctypes
import errno
from types import SimpleNamespace

import pytest

from management_common import file_permissions as private
from management_portal import shared_state


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.fixture
def mac_acl(monkeypatch):
    calls = []
    state = {"entry": 0, "errno": errno.EINVAL, "acl": 123, "query_errno": 0}
    def query(fd, kind):
        calls.append(("get", fd, kind))
        ctypes.set_errno(state["query_errno"])
        return state["acl"]
    def entry(*args):
        ctypes.set_errno(state["errno"])
        return state["entry"]
    lib = SimpleNamespace(
        acl_get_fd_np=Function(query),
        acl_valid=Function(lambda acl: 0),
        acl_get_entry=Function(entry),
        acl_free=Function(lambda acl: calls.append(("free", acl)) or 0),
    )
    monkeypatch.setattr(private, "sys", SimpleNamespace(platform="darwin"), raising=False)
    monkeypatch.setattr(private.ctypes, "CDLL", lambda *a, **k: lib)
    loader = getattr(private, "_macos_acl_api", None)
    if loader:
        loader.cache_clear()
    yield state, calls
    if loader:
        loader.cache_clear()


def test_existing_private_bytes_reject_extended_acl_without_changing_file(tmp_path, mac_acl):
    target = tmp_path / "key"
    target.write_bytes(b"unchanged")
    target.chmod(0o600)
    with pytest.raises(PermissionError):
        private.read_private_bytes(target)
    assert target.read_bytes() == b"unchanged"
    assert mac_acl[1][-1] == ("free", 123)


def test_new_private_directory_fails_before_payload_when_acl_is_inherited(tmp_path, mac_acl):
    folder = tmp_path / "private"
    with pytest.raises(PermissionError):
        private.ensure_private_directory(folder)
    assert list(folder.iterdir()) == []


def test_empty_validated_acl_allows_private_roundtrip(tmp_path, mac_acl):
    mac_acl[0]["entry"] = -1
    folder = tmp_path / "private"
    private.ensure_private_directory(folder)
    with private.create_private_file(folder / "key") as stream:
        stream.write(b"secret")
    assert private.read_private_bytes(folder / "key") == b"secret"
    assert any(call[0] == "get" and call[2] == 0x100 for call in mac_acl[1])


def test_acl_query_error_cannot_be_treated_as_empty_acl(tmp_path, mac_acl):
    target = tmp_path / "key"
    target.write_bytes(b"secret")
    target.chmod(0o600)
    mac_acl[0].update(entry=-1, errno=errno.EACCES)
    with pytest.raises(OSError):
        private.read_private_bytes(target)
    assert mac_acl[1][-1] == ("free", 123)


def test_absent_acl_on_open_descriptor_allows_private_roundtrip(tmp_path, mac_acl):
    mac_acl[0].update(acl=None, query_errno=errno.ENOENT)
    target = tmp_path / "private/key"
    private.atomic_write_private_bytes(target, b"fixture")
    assert private.read_private_bytes(target) == b"fixture"
    assert not any(call[0] == "free" for call in mac_acl[1])


@pytest.mark.parametrize("error", [0, errno.EACCES, errno.EBADF, errno.EINVAL, errno.ENOTSUP])
def test_null_acl_query_only_accepts_explicit_absence(tmp_path, mac_acl, error):
    mac_acl[0].update(acl=None, query_errno=error)
    with pytest.raises(private.PrivateFileError):
        private.atomic_write_private_bytes(tmp_path / "private/key", b"not-written")
    assert not (tmp_path / "private/key").exists()


def test_shared_private_atomic_write_checks_acl_before_payload(tmp_path, mac_acl):
    target = tmp_path / "key.json"
    target.write_bytes(b"old")
    with pytest.raises(PermissionError):
        shared_state.atomic_replace_json(target, {"secret": "never written"}, mode=0o600)
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]

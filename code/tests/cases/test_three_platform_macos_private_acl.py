"""macOS private files must not treat mode bits as an ACL check."""
import ctypes
import errno
import os
from types import SimpleNamespace

import pytest

from management_common import file_permissions as private
from management_portal import shared_state


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


OWNER_UUID = bytes(range(16))
OTHER_UUID = b"x" * 16


@pytest.fixture
def mac_acl(monkeypatch):
    calls = []
    state = {"entry": 0, "errno": errno.EINVAL, "acl": 123, "query_errno": 0,
             "entries": [(1, OTHER_UUID)], "identity_error": 0, "tag_error": 0,
             "qualifier_null": False, "valid": 0}
    qualifiers = []  # Keep allocated UUIDs alive until the mock ACL is released.
    def query(fd, kind):
        calls.append(("get", fd, kind))
        ctypes.set_errno(state["query_errno"])
        return state["acl"]
    def entry(acl, selector, output):
        assert selector in (0, -1)  # Darwin ACL_FIRST_ENTRY / ACL_NEXT_ENTRY.
        state["index"] = 0 if selector == 0 else state["index"] + 1
        ctypes.set_errno(state["errno"])
        if state["entry"] == -1 or state["index"] >= len(state["entries"]):
            return -1
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = state["index"] + 1000
        return 0
    def tag(entry, output):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_int))[0] = state["entries"][state["index"]][0]
        return state["tag_error"]
    def qualifier(entry):
        if state["qualifier_null"]:
            return None
        value = ctypes.create_string_buffer(state["entries"][state["index"]][1])
        qualifiers.append(value)
        pointer = ctypes.addressof(value)
        calls.append(("qualifier", pointer))
        return pointer
    def identity(uid, output):
        calls.append(("identity", uid))
        ctypes.memmove(output, OWNER_UUID, 16)
        return state["identity_error"]
    lib = SimpleNamespace(
        acl_get_fd_np=Function(query),
        acl_valid=Function(lambda acl: state["valid"]),
        acl_get_entry=Function(entry),
        acl_get_tag_type=Function(tag),
        acl_get_qualifier=Function(qualifier),
        mbr_uid_to_uuid=Function(identity),
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
    # tmp_path also holds the autouse site fixture's accounts/ (registration
    # locks since 2ae2514); inspect a directory that only this case writes.
    directory = tmp_path / "case"
    directory.mkdir()
    target = directory / "key.json"
    target.write_bytes(b"old")
    with pytest.raises(PermissionError):
        shared_state.atomic_replace_json(target, {"secret": "never written"}, mode=0o600)
    assert target.read_bytes() == b"old"
    assert list(directory.iterdir()) == [target]


@pytest.mark.parametrize("entries", [
    [(1, OWNER_UUID)],
    [(2, OTHER_UUID)],
    [(2, OTHER_UUID), (1, OWNER_UUID), (1, OWNER_UUID)],
])
def test_owner_grants_and_deny_entries_allow_private_roundtrip(tmp_path, mac_acl, entries):
    state, calls = mac_acl
    state["entries"] = entries
    target = tmp_path / "private/key"
    private.atomic_write_private_bytes(target, b"fixture")
    assert private.read_private_bytes(target) == b"fixture"
    assert state["entries"] == entries  # No destructive ACL replacement.
    for call in calls:
        if call[0] == "qualifier":
            assert ("free", call[1]) in calls
        if call[0] == "identity":
            assert call[1] == os.geteuid()
    if all(tag == 2 for tag, _ in entries):
        assert not any(call[0] == "identity" for call in calls)


@pytest.mark.parametrize("entries", [
    [(1, OWNER_UUID), (1, OTHER_UUID)],
    [(2, OTHER_UUID), (1, OTHER_UUID)],
    [(999, OWNER_UUID)],
])
def test_other_grants_and_unknown_entries_fail_before_payload(tmp_path, mac_acl, entries):
    mac_acl[0]["entries"] = entries
    target = tmp_path / "private/key"
    with pytest.raises(private.PrivateFileError):
        private.atomic_write_private_bytes(target, b"not-written")
    assert not target.exists()
    assert mac_acl[1][-1] == ("free", 123)


@pytest.mark.parametrize("failure", [
    {"identity_error": errno.ENOENT}, {"tag_error": -1},
    {"qualifier_null": True}, {"errno": errno.EACCES}, {"valid": -1},
])
def test_unverifiable_owner_acl_fails_closed(tmp_path, mac_acl, failure):
    mac_acl[0].update(entries=[(1, OWNER_UUID)], **failure)
    target = tmp_path / "private/key"
    with pytest.raises(private.PrivateFileError):
        private.atomic_write_private_bytes(target, b"not-written")
    assert not target.exists()
    assert mac_acl[1][-1] == ("free", 123)

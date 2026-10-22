"""Private storage rejects unsafe paths before reading bytes; no deployed keys."""
from __future__ import annotations

import importlib
import os
import stat
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from management_portal import repo_credentials_backend as credentials


class PrivateFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        if os.name == "nt":
            self.root /= "private"
            self.capability().ensure_private_directory(self.root)

    def capability(self):
        return importlib.import_module("management_common.file_permissions")

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    def test_repo_rejects_linked_key_before_reading_it(self):
        target = self.root / "target"
        target.write_bytes(credentials.Fernet.generate_key())
        target.chmod(0o600)
        link = self.root / "key"
        link.symlink_to(target)
        with self.assertRaises(credentials.RepoCredentialStoreError):
            credentials._fernet(link, create=False)

    def test_private_roundtrip_exclusive_create_and_mode(self):
        private = self.capability()
        folder = self.root / "nested" / "credentials"
        private.ensure_private_directory(folder)
        target = folder / "secret"
        with private.create_private_file(target) as stream:
            stream.write(b"fixture-data")
        self.assertEqual(private.read_private_bytes(target), b"fixture-data")
        with self.assertRaises(FileExistsError):
            with private.create_private_file(target):
                self.fail("exclusive create replaced the original")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        private.atomic_write_private_bytes(target, b"replacement")
        self.assertEqual(private.read_private_bytes(target), b"replacement")
        self.assertEqual(list(folder.iterdir()), [target])

    @unittest.skipIf(os.name == "nt", "POSIX permission fixture")
    def test_broad_file_and_ancestor_symlink_rejected_without_rewrite(self):
        private = self.capability()
        target = self.root / "secret"
        target.write_bytes(b"sensitive-fixture")
        target.chmod(0o644)
        with self.assertRaises(private.PrivateFileError):
            private.read_private_bytes(target)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(private.PrivateFileError):
            private.read_private_bytes(alias / "secret")
        with self.assertRaises(private.PrivateFileError):
            private.ensure_private_directory(alias)

    def test_replace_failure_keeps_old_bytes_and_removes_temporary(self):
        private = self.capability()
        target = self.root / "secret"
        private.atomic_write_private_bytes(target, b"original")
        with patch.object(private.os, "replace", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                private.atomic_write_private_bytes(target, b"new")
        self.assertEqual(private.read_private_bytes(target), b"original")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_temporary_collision_never_deletes_another_file(self):
        private = self.capability()
        target = self.root / "secret"
        collision = self.root / ".secret.collision.tmp"
        with private.create_private_file(collision) as stream:
            stream.write(b"another-writer")
        with patch.object(private.secrets, "token_hex", return_value="collision"):
            with self.assertRaises(FileExistsError):
                private.atomic_write_private_bytes(target, b"new")
        self.assertEqual(private.read_private_bytes(collision), b"another-writer")

    def test_windows_acl_policy_rejects_null_foreign_and_unknown_grants(self):
        private = self.capability()
        current = "S-1-5-21-1-2-3-1001"
        own = (0, 0, 0x1F01FF, current)
        private._validate_windows_acl(current, current, [own])
        private._validate_windows_acl(current, current, [own, (0, 0, 0x1F01FF, "S-1-5-18")])
        for owner, aces in [
            (current, None),
            ("S-1-5-21-1-2-3-1002", [own]),
            (current, [own, (0, 0, 1, "S-1-1-0")]),
            (current, [own, (0, 16, 1, "S-1-5-32-545")]),
            (current, [own, (9, 0, 1, current)]),
        ]:
            with self.subTest(owner=owner, aces=aces):
                with self.assertRaises(private.PrivateFileError):
                    private._validate_windows_acl(current, owner, aces)

    def test_windows_acl_accepts_python_private_directory_owner_rights(self):
        private = self.capability()
        current = "S-1-5-21-1-2-3-1001"
        # CPython 3.12.15 mkdir(mode=0700) installs
        # D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW).
        aces = [(0, 3, 0x1F01FF, sid) for sid in ("S-1-5-18", "S-1-5-32-544", "S-1-3-4")]
        private._validate_windows_acl(current, current, aces)
        # OWNER RIGHTS is safe only with the already-verified current owner.
        with self.assertRaises(private.PrivateFileError):
            private._validate_windows_acl(current, "S-1-5-21-1-2-3-1002", aces)
        for sid in ("S-1-1-0", "S-1-5-32-545", "S-1-3-0"):
            with self.subTest(sid=sid), self.assertRaises(private.PrivateFileError):
                private._validate_windows_acl(current, current, [*aces, (0, 3, 0x1F01FF, sid)])

    def test_admin_group_owner_requires_the_effective_tokens_default_owner(self):
        private = self.capability()
        user, admin = "S-1-5-21-1-2-3-1001", "S-1-5-32-544"
        aces = [(0, 3, 0x1F01FF, sid) for sid in ("S-1-5-18", admin, "S-1-3-4")]
        private._validate_windows_acl(user, admin, aces, token_owner_sid=admin)
        for owner, default, grants in ((admin, user, aces), (admin, None, aces),
                (admin, admin, None), (user + "0", admin, aces),
                (admin, admin, aces + [(0, 3, 1, "S-1-1-0")])):
            with self.subTest(owner=owner, default=default), self.assertRaises(private.PrivateFileError):
                private._validate_windows_acl(user, owner, grants, token_owner_sid=default)

    @unittest.skipUnless(os.name == "nt", "Windows DACL integration")
    def test_windows_python_private_directory_supports_secret_roundtrip(self):
        private = self.capability()
        directory = self.root / "python-private"
        directory.mkdir(mode=0o700)
        private.ensure_private_directory(directory)
        target = directory / "synthetic.key"
        private.atomic_write_private_bytes(target, b"synthetic-first")
        private.atomic_write_private_bytes(target, b"synthetic-replacement")
        self.assertEqual(private.read_private_bytes(target), b"synthetic-replacement")

    def test_windows_object_type_rejects_reparse_and_non_disk(self):
        private = self.capability()
        private._validate_windows_object(1, 0x80, directory=False)
        private._validate_windows_object(1, 0x10, directory=True)
        for kind, attributes, directory in [(1, 0x400, False), (1, 0x410, True), (2, 0x80, False), (1, 0x10, False)]:
            with self.assertRaises(private.PrivateFileError):
                private._validate_windows_object(kind, attributes, directory=directory)

    def test_windows_paths_reject_device_aliases_ads_and_drive_relative(self):
        private = self.capability()
        for path in [r"C:\Users\name\secrets\key", r"\\server\share\secrets\key", "relative/key"]:
            private._validate_windows_path(PureWindowsPath(path))
        for path in [r"C:key", r"C:\secrets\key:stream", r"C:\secrets\NUL", r"C:\secrets\key. ",
                     r"\\?\C:\key", r"\\.\PhysicalDrive0", r"C:\secret\..\key", r"C:\secrets\wild?card"]:
            with self.subTest(path=path), self.assertRaises(private.PrivateFileError):
                private._validate_windows_path(PureWindowsPath(path))

    def test_repo_encryption_actor_isolation_and_legacy_bytes_are_preserved(self):
        if os.name != "nt":
            from tests.cases.test_repo_credentials_backend import test_repo_credentials_are_encrypted_and_isolated_by_portal_user
            test_repo_credentials_are_encrypted_and_isolated_by_portal_user(self.root)
        else:
            self.capability().ensure_private_directory(self.root / "private")
            self.root /= "private"
            credentials.save_credential("fixture", {"protocol": "https", "host": "example.test", "auth_type": "token"},
                                        username="fixture", secret="isolated-secret", root=self.root / "credentials",
                                        key_path=self.root / "credential.master-key")
            self.assertEqual(credentials.list_credentials("other", root=self.root / "credentials", key_path=self.root / "credential.master-key")["credentials"], [])
        key = self.root / "credential.master-key"
        cipher = credentials.Fernet(key.read_bytes())
        record = self.root / "old.credential"
        plain = b'{"schema":"tfi.repo-git-credential.v1","actor":"fixture"}'
        encrypted = cipher.encrypt(plain)
        with self.capability().create_private_file(record) as stream:
            stream.write(encrypted)
        self.assertEqual(credentials._read_record(record, cipher)["actor"], "fixture")
        self.assertEqual(record.read_bytes(), encrypted)

    @unittest.skipIf(os.name == "nt", "POSIX exact modes")
    def test_atomic_permission_contract_precedes_payload_and_keeps_source(self):
        private = self.capability()
        target, temporary = self.root / "original", self.root / "temporary"
        target.write_bytes(b"old")
        target.chmod(0o640)
        temporary.touch()
        private.prepare_atomic_permissions(temporary, target, 0o640)
        self.assertEqual(temporary.stat().st_mode & 0o777, 0o640)
        self.assertEqual(target.read_bytes(), b"old")
        private.set_created_file_permissions(temporary, 0o600)
        self.assertEqual(temporary.stat().st_mode & 0o777, 0o600)
        self.assertEqual(temporary.read_bytes(), b"")

    def test_atomic_windows_branch_uses_existing_target_not_mode_as_acl(self):
        private = self.capability()
        class NativeBoundary:
            def __init__(self):
                self.permissions = {"old": "custom-existing-dacl", "tmp": "inherited"}
                self.fail = False

            @contextmanager
            def handle(self, path, **kwargs):
                if path.name not in self.permissions:
                    raise FileNotFoundError(path)
                yield path.name

            def apply_permissions(self, handle, mode, *, source):
                if self.fail:
                    raise PermissionError("fixture denied DACL write")
                self.permissions[handle] = self.permissions[source] if source else ("private" if mode & 0o077 == 0 else "inherit-parent")
        native = NativeBoundary()
        @contextmanager
        def directory(path, **kwargs):
            yield path, None, native
        with patch.object(private, "_directory", directory):
            private.prepare_atomic_permissions(self.root / "tmp", self.root / "old", 0o600)
            self.assertEqual(native.permissions["tmp"], "custom-existing-dacl")
            private.prepare_atomic_permissions(self.root / "tmp", self.root / "absent", 0o600)
            self.assertEqual(native.permissions["tmp"], "private")
            private.set_created_file_permissions(self.root / "tmp", 0o644)
            self.assertEqual(native.permissions["tmp"], "inherit-parent")
            native.fail = True
            with self.assertRaises(PermissionError):
                private.set_created_file_permissions(self.root / "tmp", 0o600)

    def test_windows_handle_can_query_attributes_before_validating_object(self):
        # Model the Win32 access check at the kernel boundary. Attribute queries
        # require FILE_READ_ATTRIBUTES even when READ_CONTROL was requested.
        import ctypes
        private = self.capability()
        class TagInfo(ctypes.Structure):
            _fields_ = [("attributes", ctypes.c_uint32), ("tag", ctypes.c_uint32)]
        for directory, access in ((False, 0x20000), (True, 0x20000), (False, 0xE0100)):
            with self.subTest(directory=directory, access=access):
                native = object.__new__(private._WindowsSecurity)
                native.TagInfo = TagInfo
                opened, closed = {}, []
                def open_file(path, desired_access, share, security, disposition, flags, template):
                    opened.update(access=desired_access, share=share, flags=flags)
                    return 42
                def info(handle, info_class, target, size):
                    self.assertEqual((handle, info_class, size), (42, 9, ctypes.sizeof(TagInfo)))
                    if not opened["access"] & (0x80 | 0x80000000 | 0x40000000):
                        raise PermissionError("FileAttributeTagInfo requires FILE_READ_ATTRIBUTES")
                    ctypes.cast(target, ctypes.POINTER(TagInfo)).contents.attributes = 0x10 if directory else 0x80
                    return 1
                native.open_file, native.info = open_file, info
                native.kind, native.close = lambda handle: 1, closed.append
                with native.handle(self.root / "fixture", directory=directory, access=access) as handle:
                    self.assertEqual(handle, 42)
                self.assertEqual(closed, [42])
                self.assertEqual(opened["access"] & access, access)
                self.assertEqual(opened["share"], 3)  # Still deny delete sharing.
                self.assertEqual(opened["flags"], 0x02200000)  # Inspect reparse points.

    def test_windows_native_permission_policy_uses_protected_copy_and_readonly(self):
        # Simulate Win32 boundaries, not the policy: exercise apply_permissions
        # with real pointer-shaped objects and inspect the resulting descriptor.
        import ctypes
        from types import SimpleNamespace
        private = self.capability()
        class Basic(ctypes.Structure):
            _fields_ = [("attributes", ctypes.c_uint32)]
        class Acl(ctypes.Structure):
            _fields_ = [("revision", ctypes.c_ubyte), ("reserved", ctypes.c_ubyte), ("size", ctypes.c_ushort), ("count", ctypes.c_ushort), ("reserved2", ctypes.c_ushort)]
        native = object.__new__(private._WindowsSecurity)
        native.w = SimpleNamespace(BOOL=ctypes.c_int)
        native.BasicInfo, native.Acl = Basic, Acl
        results = {}
        @contextmanager
        def security(handle):
            yield ctypes.c_void_p(13), ctypes.c_void_p(42)
        @contextmanager
        def descriptor(**kwargs):
            yield ctypes.c_void_p(99)
        def get_dacl(sd, present, dacl, defaulted):
            dacl._obj.value = 67
            return True
        def get_owner(sd, owner, defaulted):
            owner._obj.value = 13
            return True
        def set_security(handle, kind, flags, owner, group, dacl, sacl):
            results["flags"] = flags
            results["dacl"] = dacl.value
            results["owner"] = owner.value if owner else None
            if flags == 0x20000004:
                self.assertEqual(ctypes.cast(dacl, ctypes.POINTER(Acl)).contents.count, 0)
            return 0
        def get_info(handle, kind, basic, size):
            basic._obj.attributes = 0x21
            return True
        def set_info(handle, kind, basic, size):
            results["attributes"] = basic._obj.attributes
            return True
        native.security, native.descriptor = security, descriptor
        native.sd_dacl, native.sd_owner, native.set_security = get_dacl, get_owner, set_security
        native.info, native.set_info = get_info, set_info
        native.apply_permissions(1, 0o600, source=2)
        self.assertEqual(results, {"flags": 0x80000005, "dacl": 42, "owner": 13, "attributes": 0x20})
        native.apply_permissions(1, 0o400)
        self.assertEqual(results, {"flags": 0x80000005, "dacl": 67, "owner": 13, "attributes": 0x21})
        native.apply_permissions(1, 0o644)
        self.assertEqual(results["flags"], 0x20000004)
        self.assertEqual(results["attributes"], 0x20)

    @unittest.skipIf(os.name == "nt", "POSIX no-follow and descriptor lifetime")
    def test_links_non_regular_files_and_failure_do_not_leak_descriptors(self):
        private = self.capability()
        target = self.root / "secret"
        with private.create_private_file(target) as stream:
            stream.write(b"fixture")
        alias = self.root / "hardlink"
        os.link(target, alias)
        with self.assertRaises(private.PrivateFileError):
            private.read_private_bytes(target)
        with self.assertRaises(private.PrivateFileError):
            private.set_created_file_permissions(alias, 0o600)
        alias.unlink()
        fifo = self.root / "pipe"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(private.PrivateFileError):
            private.read_private_bytes(fifo)
        fd_root = Path("/proc/self/fd")
        if fd_root.is_dir():
            before = len(list(fd_root.iterdir()))
            for _ in range(10):
                with self.assertRaises(private.PrivateFileError):
                    private.read_private_bytes(fifo)
            self.assertEqual(len(list(fd_root.iterdir())), before)

    @unittest.skipUnless(os.name == "nt", "requires a Windows kernel; not simulated")
    def test_native_windows_private_acl_and_atomic_copy(self):
        private = self.capability()
        folder = self.root / "acl-native"
        private.ensure_private_directory(folder)
        target, temporary = folder / "target", folder / "temporary"
        with private.create_private_file(target) as stream:
            stream.write(b"original")
        temporary.touch()
        private.prepare_atomic_permissions(temporary, target, 0o600)
        private.assert_private_file(temporary)
        self.assertEqual(private.read_private_bytes(target), b"original")
        private.set_created_file_permissions(temporary, 0o400)
        self.assertTrue(temporary.stat().st_file_attributes & 1)
        private.set_created_file_permissions(temporary, 0o600)
        private.assert_private_file(temporary)


if __name__ == "__main__":
    unittest.main()

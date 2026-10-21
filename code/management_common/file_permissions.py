"""Private storage and atomic-write permissions, with no optional dependencies.

Private APIs check the opened object *before* reading its contents. Windows
objects use a protected current-user DACL at creation; existing objects must be
owned by that user and may grant access only to that user, SYSTEM or local
Administrators. NULL DACLs and unrecognised ACEs fail closed. POSIX uses the
existing 0700/0600 convention plus ownership and no-follow checks. macOS private
objects with extended ACLs are rejected rather than trusting mode bits alone.

The atomic permission APIs also support ordinary, non-private files. Call them
on an empty temporary file before writing payload bytes. They never assert that
a general-purpose target is private.
"""
from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
import sys
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import Path, PureWindowsPath


class PrivateFileError(PermissionError):
    """A filesystem object cannot safely hold private material."""


@lru_cache(maxsize=1)
def _macos_acl_api():
    library = ctypes.CDLL(None, use_errno=True)
    for name, args, result in (
        ("acl_get_fd_np", [ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
        ("acl_valid", [ctypes.c_void_p], ctypes.c_int),
        ("acl_get_entry", [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
        ("acl_free", [ctypes.c_void_p], ctypes.c_int),
    ):
        function = getattr(library, name)
        function.argtypes, function.restype = args, result
    return library


def assert_private_posix_acl(descriptor: int) -> None:
    """macOS ACLs can grant access independently of mode bits; reject them.

    No ACL is removed or rewritten. An unsupported/unreadable ACL fails closed,
    including on newly created objects which inherited it from their parent.
    Linux POSIX access ACL grants are constrained by the existing mode check.
    """
    if sys.platform != "darwin":
        return
    library = _macos_acl_api()
    ctypes.set_errno(0)
    acl = library.acl_get_fd_np(descriptor, 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        error = ctypes.get_errno()
        # Darwin filesec_get_property(FILESEC_ACL) reports ENOENT when the
        # opened object has no ACL. This is not a path lookup: the caller has
        # already pinned and checked this descriptor's identity and mode.
        if error == errno.ENOENT:
            return
        raise PrivateFileError(error, "Cannot verify private file extended ACL")
    try:
        if library.acl_valid(acl) != 0:
            raise PrivateFileError("Private file has an invalid extended ACL")
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = library.acl_get_entry(acl, 0, ctypes.byref(entry))  # ACL_FIRST_ENTRY
        if result == 0:
            raise PrivateFileError("Private file has an extended ACL")
        # Darwin returns -1/EINVAL at the end of a validated, empty ACL.
        if result != -1 or ctypes.get_errno() != errno.EINVAL:
            raise PrivateFileError("Cannot enumerate private file extended ACL")
    finally:
        library.acl_free(acl)


def _validate_windows_acl(current_sid, owner_sid, aces):
    if owner_sid != current_sid or aces is None:
        raise PrivateFileError("Private object has a foreign owner or NULL DACL")
    trusted = {current_sid, "S-1-5-18", "S-1-5-32-544"}
    for ace_type, flags, mask, sid in aces:
        # Unknown/object/callback ACEs are deliberately not approximated.
        if ace_type not in (0, 1):
            raise PrivateFileError("Private object contains an unsupported ACE")
        if ace_type == 0 and mask and sid not in trusted:
            # Also reject inherit-only grants: children must remain private.
            raise PrivateFileError("Private object grants access to another principal")


def _validate_windows_object(kind, attributes, *, directory):
    if kind != 1 or attributes & 0x400 or bool(attributes & 0x10) != directory:
        raise PrivateFileError("Private storage requires a regular, non-reparse object")


class _WindowsSecurity:
    """Small Win32 adapter. All security inspection is by an open handle."""

    def __init__(self):
        from ctypes import wintypes as w
        self.w = w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        p = ctypes.c_void_p

        class Attributes(ctypes.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", p), ("inherit", w.BOOL)]

        class TagInfo(ctypes.Structure):
            _fields_ = [("attributes", w.DWORD), ("tag", w.DWORD)]

        class BasicInfo(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("creation", "access", "write", "change")] + [("attributes", w.DWORD)]

        class Acl(ctypes.Structure):
            _fields_ = [("revision", w.BYTE), ("reserved", w.BYTE), ("size", w.WORD), ("count", w.WORD), ("reserved2", w.WORD)]

        class Ace(ctypes.Structure):
            _fields_ = [("type", w.BYTE), ("flags", w.BYTE), ("size", w.WORD), ("mask", w.DWORD), ("sid", w.DWORD)]

        self.Attributes, self.TagInfo, self.BasicInfo, self.Acl, self.Ace = Attributes, TagInfo, BasicInfo, Acl, Ace
        def bind(dll, name, args, result):
            f = getattr(dll, name)
            f.argtypes, f.restype = args, result
            return f
        self.open_file = bind(self.kernel, "CreateFileW", [w.LPCWSTR, w.DWORD, w.DWORD, p, w.DWORD, w.DWORD, p], p)
        self.mkdir = bind(self.kernel, "CreateDirectoryW", [w.LPCWSTR, p], w.BOOL)
        self.close = bind(self.kernel, "CloseHandle", [p], w.BOOL)
        self.free = bind(self.kernel, "LocalFree", [p], p)
        self.kind = bind(self.kernel, "GetFileType", [p], w.DWORD)
        self.info = bind(self.kernel, "GetFileInformationByHandleEx", [p, ctypes.c_int, p, w.DWORD], w.BOOL)
        self.set_info = bind(self.kernel, "SetFileInformationByHandle", [p, ctypes.c_int, p, w.DWORD], w.BOOL)
        self.process = bind(self.kernel, "GetCurrentProcess", [], p)
        self.thread = bind(self.kernel, "GetCurrentThread", [], p)
        self.duplicate = bind(self.kernel, "DuplicateHandle", [p, p, p, p, w.DWORD, w.BOOL, w.DWORD], w.BOOL)
        self.process_token = bind(self.advapi, "OpenProcessToken", [p, w.DWORD, p], w.BOOL)
        self.thread_token = bind(self.advapi, "OpenThreadToken", [p, w.DWORD, w.BOOL, p], w.BOOL)
        self.token_info = bind(self.advapi, "GetTokenInformation", [p, ctypes.c_int, p, w.DWORD, p], w.BOOL)
        self.sid_string = bind(self.advapi, "ConvertSidToStringSidW", [p, p], w.BOOL)
        self.from_sddl = bind(self.advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", [w.LPCWSTR, w.DWORD, p, p], w.BOOL)
        self.get_security = bind(self.advapi, "GetSecurityInfo", [p, ctypes.c_int, w.DWORD, p, p, p, p, p], w.DWORD)
        self.set_security = bind(self.advapi, "SetSecurityInfo", [p, ctypes.c_int, w.DWORD, p, p, p, p], w.DWORD)
        self.get_ace = bind(self.advapi, "GetAce", [p, w.DWORD, p], w.BOOL)
        self.sd_dacl = bind(self.advapi, "GetSecurityDescriptorDacl", [p, p, p, p], w.BOOL)
        self.sd_owner = bind(self.advapi, "GetSecurityDescriptorOwner", [p, p, p], w.BOOL)
        self.current_sid = self._current_sid()

    @staticmethod
    def checked(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def sid_text(self, sid):
        text = ctypes.c_void_p()
        self.checked(self.sid_string(sid, ctypes.byref(text)))
        try:
            return ctypes.wstring_at(text)
        finally:
            self.free(text)

    def _current_sid(self):
        token = ctypes.c_void_p()
        if not self.thread_token(self.thread(), 8, True, ctypes.byref(token)):
            if ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
                self.checked(False)
            self.checked(self.process_token(self.process(), 8, ctypes.byref(token)))
        try:
            size = self.w.DWORD()
            self.token_info(token, 1, None, 0, ctypes.byref(size))
            if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
                self.checked(False)
            buffer = ctypes.create_string_buffer(size.value)
            self.checked(self.token_info(token, 1, buffer, size, ctypes.byref(size)))
            return self.sid_text(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0])
        finally:
            self.close(token)

    @contextmanager
    def descriptor(self, *, directory=False):
        descriptor = ctypes.c_void_p()
        inherit = "OICI" if directory else ""
        self.checked(self.from_sddl(f"O:{self.current_sid}D:P(A;{inherit};FA;;;{self.current_sid})", 1, ctypes.byref(descriptor), None))
        try:
            yield descriptor
        finally:
            self.free(descriptor)

    @contextmanager
    def handle(self, path, *, directory=False, create=False, access=0x20000):
        with ExitStack() as stack:
            attributes = None
            if create:
                sd = stack.enter_context(self.descriptor(directory=directory))
                attributes = self.Attributes(ctypes.sizeof(self.Attributes), sd, False)
                if directory:
                    self.checked(self.mkdir(str(path), ctypes.byref(attributes)))
            # No FILE_SHARE_DELETE: every ancestor stays pinned while in use.
            handle = self.open_file(str(path), access, 3, ctypes.byref(attributes) if attributes else None,
                                    1 if create and not directory else 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                self.checked(False)
            try:
                tag = self.TagInfo()
                self.checked(self.info(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)))
                _validate_windows_object(self.kind(handle), tag.attributes, directory=directory)
                yield handle
            finally:
                self.close(handle)

    @contextmanager
    def security(self, handle):
        owner, dacl, sd = (ctypes.c_void_p() for _ in range(3))
        result = self.get_security(handle, 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(sd))
        if result:
            raise ctypes.WinError(result)
        try:
            yield owner, dacl
        finally:
            self.free(sd)

    def assert_private(self, handle):
        with self.security(handle) as (owner, dacl):
            aces = None
            if dacl:
                aces = []
                acl = ctypes.cast(dacl, ctypes.POINTER(self.Acl)).contents
                for index in range(acl.count):
                    pointer = ctypes.c_void_p()
                    self.checked(self.get_ace(dacl, index, ctypes.byref(pointer)))
                    ace = ctypes.cast(pointer, ctypes.POINTER(self.Ace)).contents
                    if ace.type not in (0, 1) or ace.size < 16:
                        raise PrivateFileError("Private object contains an unsupported ACE")
                    sid = self.sid_text(pointer.value + self.Ace.sid.offset)
                    aces.append((ace.type, ace.flags, ace.mask, sid))
            _validate_windows_acl(self.current_sid, self.sid_text(owner), aces)

    def file_descriptor(self, handle, *, writable):
        import msvcrt
        duplicate = ctypes.c_void_p()
        self.checked(self.duplicate(self.process(), handle, self.process(), ctypes.byref(duplicate), 0, False, 2))
        try:
            return msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY))
        except BaseException:
            self.close(duplicate)
            raise

    def apply_permissions(self, handle, mode, *, source=None):
        with ExitStack() as stack:
            owner = None
            flags = 4 | 0x80000000  # DACL_SECURITY_INFORMATION | PROTECTED
            if source is not None:
                owner, dacl = stack.enter_context(self.security(source))
                # Do not confer ownership on the new inode if the old owner
                # differs. Setting that SID may fail without privileges: safe.
                flags |= 1
            elif mode & 0o077 == 0:
                sd = stack.enter_context(self.descriptor())
                present, defaulted, dacl = self.w.BOOL(), self.w.BOOL(), ctypes.c_void_p()
                self.checked(self.sd_dacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
                owner = ctypes.c_void_p()
                self.checked(self.sd_owner(sd, ctypes.byref(owner), ctypes.byref(defaulted)))
                flags |= 1
            else:
                # Empty (not NULL), unprotected DACL asks Windows to inherit
                # the parent ACL. See SetSecurityInfo inheritance contract.
                empty = self.Acl(2, 0, ctypes.sizeof(self.Acl), 0, 0)
                dacl = ctypes.cast(ctypes.pointer(empty), ctypes.c_void_p)
                flags = 4 | 0x20000000
            result = self.set_security(handle, 1, flags, owner, None, dacl, None)
            if result:
                raise ctypes.WinError(result)
        basic = self.BasicInfo()
        self.checked(self.info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)))
        basic.attributes = (basic.attributes | 1) if mode & 0o200 == 0 else (basic.attributes & ~1)
        if not basic.attributes:
            basic.attributes = 0x80
        self.checked(self.set_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)))


def _validate_windows_path(path: PureWindowsPath):
    if path.drive and not path.root:
        raise PrivateFileError("Drive-relative private storage paths are ambiguous")
    if str(path).startswith(("\\\\?\\", "\\\\.\\")):
        raise PrivateFileError("Device paths are not supported for private storage")
    for part in path.parts[1:] if path.anchor else path.parts:
        reserved = getattr(os.path, "isreserved", lambda name: PureWindowsPath(name).is_reserved())
        if (part == ".." or any(c in ':<>"|?*' or ord(c) < 32 for c in part)
                or part.endswith((".", " ")) or reserved(part)):
            raise PrivateFileError("Ambiguous Windows private storage path")


def _absolute(path):
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise PrivateFileError("Parent traversal is not allowed in private storage paths")
    if os.name == "nt":
        _validate_windows_path(path)
    return Path(os.path.abspath(path))


@contextmanager
def _directory(path, *, create=False, private=False):
    """Pin ancestors without following links; private applies to the leaf."""
    path = _absolute(path)
    with ExitStack() as stack:
        if os.name == "nt":
            win = _WindowsSecurity()
            current = Path(path.anchor)
            handle = stack.enter_context(win.handle(current, directory=True))
            for part in path.parts[1:]:
                current /= part
                try:
                    handle = stack.enter_context(win.handle(current, directory=True))
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        handle = stack.enter_context(win.handle(current, directory=True, create=True))
                    except FileExistsError:
                        handle = stack.enter_context(win.handle(current, directory=True))
            if private:
                win.assert_private(handle)
            yield path, None, win
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(path.anchor, flags)
            stack.callback(os.close, descriptor)
            for part in path.parts[1:]:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(part, flags, dir_fd=descriptor)
                except OSError as exc:
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise PrivateFileError("Private directory path contains a link or non-directory") from exc
                    raise
                descriptor = child
                stack.callback(os.close, descriptor)
            if private:
                if os.fstat(descriptor).st_uid != os.geteuid():
                    raise PrivateFileError("Private directory belongs to another user")
                assert_private_posix_acl(descriptor)
                os.fchmod(descriptor, 0o700)
            yield path, descriptor, None


def ensure_private_directory(path):
    """Create owner-only directories; reject unsafe Windows ACLs, never repair them."""
    with _directory(path, create=True, private=True):
        pass


@contextmanager
def _private_stream(path, *, create=False):
    path = _absolute(path)
    with _directory(path.parent, private=create) as (parent, parent_fd, win):
        if win:
            with win.handle(path, create=create, access=0x20000 | (0xC0000000 if create else 0x80000000)) as handle:
                win.assert_private(handle)
                fd = win.file_descriptor(handle, writable=create)
                with os.fdopen(fd, "w+b" if create else "rb") as stream:
                    yield stream
        else:
            flags = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
            flags |= (os.O_RDWR | os.O_CREAT | os.O_EXCL) if create else os.O_RDONLY
            try:
                fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise PrivateFileError("Private file is a symbolic link") from exc
                raise
            with os.fdopen(fd, "w+b" if create else "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                    raise PrivateFileError("Private file type, ownership or permissions are unsafe")
                assert_private_posix_acl(stream.fileno())
                if create:
                    os.fchmod(stream.fileno(), 0o600)
                yield stream


def create_private_file(path):
    """Exclusive binary stream, secure at creation; caller flushes/fsyncs as needed."""
    return _private_stream(path, create=True)


def assert_private_file(path):
    with _private_stream(path):
        pass


def read_private_bytes(path, *, required_mode=None):
    """Read after identity/ACL checks; optionally retain an exact POSIX mode contract."""
    with _private_stream(path) as stream:
        if required_mode is not None and os.name != "nt":
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != required_mode:
                raise PrivateFileError("Private file has an unexpected mode")
        return stream.read()


def atomic_write_private_bytes(path, payload):
    """Replace through an owner-only sibling; preserve the old file on failure."""
    path = _absolute(path)
    with _directory(path.parent, create=True, private=True) as (_, parent_fd, win):
        try:
            assert_private_file(path)
        except FileNotFoundError:
            pass
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
        created = False
        try:
            with create_private_file(temporary) as stream:
                created = True
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if win:
                os.replace(temporary, path)
            else:
                os.replace(temporary.name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            if created:
                if win:
                    temporary.unlink(missing_ok=True)
                else:
                    try:
                        os.unlink(temporary.name, dir_fd=parent_fd)
                    except FileNotFoundError:
                        pass


def _set_permissions(path, mode, target=None):
    path = _absolute(path)
    with _directory(path.parent) as (_, parent_fd, win):
        if win:
            with ExitStack() as stack:
                handle = stack.enter_context(win.handle(path, access=0xE0100))  # READ_CONTROL, WRITE_DAC/OWNER/ATTRIBUTES
                source = None
                if target is not None:
                    target = _absolute(target)
                    stack.enter_context(_directory(target.parent))
                    try:
                        source = stack.enter_context(win.handle(target))
                    except FileNotFoundError:
                        pass
                win.apply_permissions(handle, mode, source=source)
        else:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                    raise PrivateFileError("Atomic temporary must be an owned regular file with one link")
                os.fchmod(descriptor, mode)
                if mode & 0o077 == 0:
                    assert_private_posix_acl(descriptor)
            finally:
                os.close(descriptor)


def prepare_atomic_permissions(temporary: Path, target: Path, mode: int) -> None:
    """Before payload: POSIX chmod; Windows copy target owner/DACL or new-file policy.

    Windows copies a protected snapshot of the target DACL to avoid adding
    inherited permissions. Missing targets follow set_created_file_permissions.
    The caller owns atomic replacement/CAS and must keep a stable target during
    this operation. The source target is never modified.
    """
    _set_permissions(temporary, mode, target=target)


def set_created_file_permissions(path: Path, mode: int) -> None:
    """Before payload: owner-only DACL for private modes, parent ACL otherwise.

    Windows readonly follows owner-write mode; POSIX applies the exact mode.
    The caller must have exclusively created an empty regular file.
    """
    _set_permissions(path, mode)

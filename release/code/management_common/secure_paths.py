"""No-follow, pinned filesystem access for resource files and workspace writes.

POSIX uses openat-style directory descriptors. Windows holds every ancestor
without FILE_SHARE_DELETE so a name cannot be renamed beneath the pinned path;
every opened handle is checked for reparse points before any content is read.
"""
from __future__ import annotations

import errno
import os
import stat
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import Path, PureWindowsPath


def relative_parts(value: str) -> tuple[str, ...]:
    """Parse slash-separated resource names with native filename semantics."""
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts) or "\x00" in value:
        raise ValueError("File address requires a relative path without empty or dot segments")
    if os.name == "nt" and any(
        any(c in '\\:<>"|?*' or ord(c) < 32 for c in part)
        or PureWindowsPath(part).is_reserved() or part.endswith((" ", "."))
        for part in parts
    ):
        raise ValueError("File address names a reserved Windows path")
    return parts


def _part(name: str) -> str:
    if relative_parts(name) != (name,):
        raise ValueError("Expected one file name")
    return name


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _windows_open(path, flags: int, *, directory: bool = False) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = _windows_api()
    write = bool(flags & (os.O_WRONLY | os.O_RDWR))
    access = 0xC0000000 if flags & os.O_RDWR else (0x40000000 if write else 0x80000000)
    creation = (1 if flags & os.O_EXCL else 4) if flags & os.O_CREAT else 3
    if flags & os.O_TRUNC:
        raise ValueError("Pinned files must not be truncated during open")
    # No SHARE_DELETE: opened names stay put. Directory handles also exclude
    # writers so an ancestor cannot be changed into a reparse point in place.
    # Regular files retain concurrent read/write semantics like POSIX.
    sharing = 0x1 if directory else 0x1 | 0x2
    handle = kernel.CreateFileW(str(path), access, sharing, None, creation, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = (wintypes.DWORD * 2)()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes[0] & 0x400:
            raise ValueError("File path contains a reparse point")
        if directory and not attributes[0] & 0x10:
            raise NotADirectoryError(str(path))
        if not directory and attributes[0] & 0x10:
            # A substituted directory needs the stricter sharing contract;
            # never enumerate it using a handle opened as a regular file.
            raise IsADirectoryError(str(path))
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if flags & os.O_RDWR else os.O_WRONLY if write else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return fd  # CRT owns the handle now; os.close(fd) releases it.


def directory_path(fd: int) -> Path:
    """Native path of a Windows directory pinned by open_directory."""
    import ctypes
    import msvcrt

    kernel = _windows_api()
    handle = msvcrt.get_osfhandle(fd)
    size = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size + 1)
    written = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise OSError("Unable to resolve pinned directory handle")
    return Path(buffer.value)


def open_child(directory: int, name: str, flags: int, mode: int = 0o664, *, is_directory: bool = False) -> int:
    _part(name)
    if os.name == "nt":
        return _windows_open(directory_path(directory) / name, flags, directory=is_directory)
    flags |= os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if is_directory:
        flags |= os.O_DIRECTORY
    return os.open(name, flags, mode, dir_fd=directory)


def stat_child(directory: int, name: str):
    _part(name)
    if os.name == "nt":
        info = (directory_path(directory) / name).lstat()
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("File path contains a reparse point")
        return info
    return os.stat(name, dir_fd=directory, follow_symlinks=False)


def replace_child(directory: int, source: str, target: str) -> None:
    _part(source)
    _part(target)
    if os.name == "nt":
        parent = directory_path(directory)
        os.replace(parent / source, parent / target)
    else:
        os.replace(source, target, src_dir_fd=directory, dst_dir_fd=directory)


def unlink_child(directory: int, name: str) -> None:
    _part(name)
    if os.name == "nt":
        (directory_path(directory) / name).unlink()
    else:
        os.unlink(name, dir_fd=directory)


@contextmanager
def open_directory(root, parts: tuple[str, ...] = (), *, create: bool = False):
    """Pin an absolute root and optional relative child directories.

Only child directories may be created; a missing registered root is an error.
Every ancestor is kept open until the context exits.
"""
    root = Path(str(root))
    if not root.is_absolute() or (os.name == "nt" and str(root).startswith(("\\\\.\\", "\\\\?\\"))):
        raise ValueError("An absolute native filesystem root is required")
    for part in parts:
        _part(part)
    with ExitStack() as stack:
        def keep(fd):
            stack.callback(os.close, fd)
            return fd
        try:
            if os.name == "nt":
                directory = keep(_windows_open(root.anchor, os.O_RDONLY, directory=True))
            else:
                directory = keep(os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)))
            for part, may_create in [*((part, False) for part in root.parts[1:]), *((part, create) for part in parts)]:
                try:
                    child = open_child(directory, part, os.O_RDONLY, is_directory=True)
                except FileNotFoundError:
                    if not may_create:
                        raise
                    try:
                        if os.name == "nt":
                            (directory_path(directory) / part).mkdir(mode=0o775)
                        else:
                            os.mkdir(part, 0o775, dir_fd=directory)
                    except FileExistsError:
                        pass
                    child = open_child(directory, part, os.O_RDONLY, is_directory=True)
                directory = keep(child)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("File path contains a symlink or non-directory component") from exc
            raise
        yield directory


@contextmanager
def open_target(root, parts: tuple[str, ...]):
    with open_directory(root, parts[:-1]) as directory:
        if not parts:
            yield directory, os.fstat(directory)
            return
        try:
            before = stat_child(directory, parts[-1])
            if stat.S_ISLNK(before.st_mode):
                raise ValueError("File path contains a symlink")
            if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
                raise ValueError("File address requires a regular file or a directory")
            # Nonblocking prevents a substituted FIFO from hanging the reader;
            # only regular files/directories are exposed after fstat.
            fd = open_child(directory, parts[-1], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0), is_directory=stat.S_ISDIR(before.st_mode))
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ValueError("File path contains a symlink or non-directory component") from exc
            raise
        try:
            info = os.fstat(fd)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("File address requires a regular file or a directory")
            yield fd, info
        finally:
            os.close(fd)


def prepare_child_permissions(directory: int, temporary: str, target: str, mode: int) -> None:
    """Preserve the Windows target ACL before writing the atomic temporary.

    POSIX creation already applies its original mode and umask through openat.
    On Windows the pinned parent keeps both names stable for the ACL owner.
    """
    _part(temporary)
    _part(target)
    if os.name == "nt":
        from management_common.file_permissions import prepare_atomic_permissions
        parent = directory_path(directory)
        # GetFinalPathNameByHandleW emits an extended DOS path. The ACL owner
        # accepts ordinary drive/UNC paths, whose ancestors we still hold.
        native = str(parent)
        if native.startswith("\\\\?\\UNC\\"):
            parent = Path("\\\\" + native[8:])
        elif native.startswith("\\\\?\\") and PureWindowsPath(native[4:]).drive.endswith(":"):
            parent = Path(native[4:])
        prepare_atomic_permissions(parent / temporary, parent / target, mode)


def scandir(directory: int):
    return os.scandir(directory_path(directory) if os.name == "nt" else directory)


def directory_unchanged(directory: int, path: Path) -> bool:
    try:
        return path.resolve(strict=True) == path and os.path.samestat(os.fstat(directory), path.stat(follow_symlinks=False))
    except (FileNotFoundError, NotADirectoryError, RuntimeError):
        return False


@contextmanager
def lock_directory(directory: int):
    """Keep compare-and-replace serialized on the stable parent directory."""
    if os.name != "nt":
        import fcntl
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(directory, fcntl.LOCK_UN)
    else:
        from management_common.file_lock import lock_stream
        # Fixed name under a pinned directory; never removed on unlock.
        fd = open_child(directory, ".portal-write.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+b") as stream, lock_stream(stream):
            yield

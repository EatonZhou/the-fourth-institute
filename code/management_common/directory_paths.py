"""Filesystem boundaries shared by directory creation, moves and cleanup."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys

from management_common.filesystem_paths import filesystem_path
from .instance_manifest import AREAS, current_instance


_ASCII_LETTER = re.compile('[a-zA-Z]')
_ASCII_SWAPCASE = str.maketrans(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz',
)


def lexical_path(value: str | Path) -> Path:
    """Keep the final directory entry, including a replacement symbolic link."""
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts or '\x00' in str(path):
        raise ValueError('Directory location must be an absolute path without parent traversal')
    return Path(os.path.abspath(path))


def cleanup_location(value: str | Path) -> str:
    path = lexical_path(value)
    instance = current_instance()
    for area in AREAS:
        root = instance.area(area)
        if path.is_relative_to(root):
            relative = path.relative_to(root).as_posix()
            return area + ':' + ('' if relative == '.' else relative)
    return path.as_posix()


def cleanup_path(location: str, *, instance=None) -> Path:
    area, separator, relative = location.partition(':')
    if separator and area in AREAS:
        from .secure_paths import relative_parts
        parts = relative_parts(relative) if relative else ()
        return (current_instance() if instance is None else instance).area(area).joinpath(*parts)
    return lexical_path(location)


def validate_removal(path: Path) -> None:
    """Never follow a redirected parent or remove a host instance's boundary."""
    from .host_instances import read_registry
    from .instance_manifest import load, overlaps

    path = lexical_path(path)
    instance = current_instance()
    protected = [instance.root, *(instance.area(area) for area in AREAS)]
    if any(path == root or path in root.parents for root in protected):
        raise ValueError('Cannot delete an instance root, area root or their ancestor')
    if path.parent.resolve() != path.parent:
        raise ValueError('Cleanup parent has been redirected by a symbolic link')
    for name, root in read_registry().items():
        if name == instance.name and Path(root) == instance.root:
            continue
        other = load(root)
        if any(overlaps(path, boundary) for boundary in
               [other.root, *(other.area(area) for area in AREAS)]):
            raise ValueError('Cannot delete a directory belonging to another instance')


def remove_path(path: Path, *, identity=None) -> None:
    """Delete directory entries relative to pinned, non-following ancestors.

    Identity claims include durable recovery even if the public name is gone.
    Without a claim, delete the current tree through pinned parent handles.
    """
    if identity is not None:
        from .owned_path_deletion import OwnedPathDeletionStore
        OwnedPathDeletionStore().remove(path, identity)
        return
    from .secure_paths import open_directory
    path = lexical_path(path)
    try:
        with open_directory(path.parent) as parent:
            validate_removal(path)
            _remove_entry(parent, path.name, identity=identity)
    except FileNotFoundError:
        return


def _remove_entry(parent, name, *, identity=None):
    from .secure_paths import directory_path, open_child, scandir, unlink_child
    # Windows stat_child rejects reparse points for reads. Cleanup instead
    # needs their lstat attributes so it can remove the link entry itself.
    try:
        before = (filesystem_path(directory_path(parent) / name).lstat() if os.name == 'nt'
                  else os.stat(name, dir_fd=parent, follow_symlinks=False))
    except FileNotFoundError:
        return
    if identity is not None and [before.st_dev, before.st_ino] != list(identity):
        raise FileExistsError('Directory entry ownership changed during cleanup: ' + name)
    linked = stat.S_ISLNK(before.st_mode) or bool(getattr(before, 'st_file_attributes', 0) & 0x400)
    if linked or not stat.S_ISDIR(before.st_mode):
        if os.name == 'nt' and linked and stat.S_ISDIR(before.st_mode):
            os.rmdir(directory_path(parent) / name)
        else:
            unlink_child(parent, name)
        return
    try:
        child = open_child(parent, name, os.O_RDONLY, is_directory=True)
    except PermissionError:
        _restore_owned_directory(parent, name, before)
        child = open_child(parent, name, os.O_RDONLY, is_directory=True)
    try:
        current = os.fstat(child)
        if not os.path.samestat(before, current):
            raise FileExistsError('Directory entry changed during cleanup: ' + name)
        # An Agent may seal its own results read-only. Change only the pinned
        # inode owned by this user, never a name that could become a symlink.
        if os.name != 'nt' and current.st_uid == os.geteuid() and current.st_mode & stat.S_IRWXU != stat.S_IRWXU:
            os.fchmod(child, stat.S_IMODE(current.st_mode) | stat.S_IRWXU)
        with scandir(child) as entries:
            names = [entry.name for entry in entries]
        for entry in names:
            _remove_entry(child, entry)
    finally:
        os.close(child)
    # A replacement after close can only make rmdir fail or remove an empty
    # entry; recursion above has already finished against the original inode.
    if os.name == 'nt':
        os.rmdir(directory_path(parent) / name)
    else:
        os.rmdir(name, dir_fd=parent)


def _restore_owned_directory(parent, name, expected):
    """Open unreadable directories without following names while changing mode."""
    from .secure_paths import open_child
    flags = getattr(os, 'O_PATH', getattr(os, 'O_EVTONLY', None))
    if os.name == 'nt' or flags is None:
        raise PermissionError('Directory is not readable: ' + name)
    descriptor = open_child(parent, name, flags, is_directory=True)
    try:
        current = os.fstat(descriptor)
        if not os.path.samestat(expected, current) or current.st_uid != os.geteuid():
            raise PermissionError('Directory ownership changed during cleanup: ' + name)
        mode = stat.S_IMODE(current.st_mode) | stat.S_IRWXU
        if hasattr(os, 'O_PATH'):
            # O_PATH cannot be fchmod'ed. This kernel-owned magic link names
            # the open inode, even if the original directory was renamed.
            os.chmod('/proc/self/fd/' + str(descriptor), mode)
        else:
            os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def location_key(path: Path) -> tuple[str, ...]:
    """Lexical entry identity, using each parent directory's native case rule.

    Keep links and hard links as separate entries: inode identity is not a
    directory hierarchy. Missing suffixes inherit the nearest existing
    directory's rule, so reservations work before any files are created.
    """
    return _location_key(path, _directory_case_sensitive)


def _location_key(path, case_sensitive):
    path = lexical_path(path)
    parent = Path(path.anchor)
    result = [path.anchor.casefold() if os.name == 'nt' else path.anchor]
    for part in path.parts[1:]:
        result.append(part if case_sensitive(parent) else part.casefold())
        parent /= part
    return tuple(result)


def _directory_case_sensitive(path: Path) -> bool:
    """Read the actual directory/volume policy without creating probe files.

    Do not cache across calls: mounts and Windows/Linux directory flags can
    change. Unsupported filesystems must provide observable entry evidence;
    otherwise report that the case rule cannot be determined.
    """
    requested = path
    while True:
        try:
            status = filesystem_path(path).stat()
            if stat.S_ISDIR(status.st_mode):
                break
        except (FileNotFoundError, NotADirectoryError):
            pass
        if path == path.parent:
            raise OSError('Cannot find an existing directory for: ' + str(path))
        path = path.parent

    sensitive = _existing_directory_case_sensitive(path)
    if os.name == 'nt' and sensitive and str(path) != str(requested):
        # Windows creation APIs / WSL versions differ in flag inheritance.
        raise OSError('Cannot determine case policy below a missing Windows directory: ' + str(requested))
    return sensitive


def _existing_directory_case_sensitive(path: Path) -> bool:
    # Ask the filesystem to look up an alternate spelling of an existing
    # entry. No writes and no platform assumptions (including network mounts).
    with os.scandir(filesystem_path(path)) as entries:
        for entry in entries:
            # Only ASCII letters yield evidence for this lookup. Large
            # directories of Chinese/numeric names otherwise spend Python
            # work on every character without making a single useful probe.
            if not _ASCII_LETTER.search(entry.name):
                continue
            alternate = (entry.name.swapcase() if entry.name.isascii()
                         else entry.name.translate(_ASCII_SWAPCASE))
            # Windows DirEntry.stat() may omit st_dev/st_ino (both zero).
            # Compare two path-based queries, which include file identity,
            # rather than mistaking incomplete enumeration metadata for a
            # distinct entry and declaring an insensitive directory sensitive.
            original = filesystem_path(path / entry.name).lstat()
            try:
                other = filesystem_path(path / alternate).lstat()
            except FileNotFoundError:
                return True
            if not os.path.samestat(original, other):
                return True
            # Same inode alone is insufficient: distinct hard-link entries
            # must not be mistaken for alternate spellings of one entry.
            return any(item.name == alternate for item in filesystem_path(path).iterdir())

    # Empty/numeric-only directories need a native policy query.
    if sys.platform == 'darwin':
        import ctypes
        import struct
        # getattrlist(ATTR_VOL_INFO | ATTR_VOL_CAPABILITIES), from sys/attr.h.
        attributes = struct.pack('=HHIIIII', 5, 0, 0, 0x80020000, 0, 0, 0)
        capabilities = (ctypes.c_uint32 * 9)()  # length, capabilities[4], valid[4]
        function = ctypes.CDLL(None, use_errno=True).getattrlist
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong]
        function.restype = ctypes.c_int
        if function(os.fsencode(path), attributes, capabilities, ctypes.sizeof(capabilities), 0) == 0:
            if capabilities[0] >= ctypes.sizeof(capabilities) and capabilities[5] & 0x100:
                return bool(capabilities[1] & 0x100)  # VOL_CAP_FMT_CASE_SENSITIVE
    elif os.name == 'nt':
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from .secure_paths import _windows_api, open_directory
        with open_directory(path) as descriptor:
            flags = wintypes.DWORD()
            if _windows_api().GetFileInformationByHandleEx(
                    msvcrt.get_osfhandle(descriptor), 23, ctypes.byref(flags), ctypes.sizeof(flags)):
                return bool(flags.value & 1)  # FILE_CS_FLAG_CASE_SENSITIVE_DIR
            error = ctypes.get_last_error()
            if error not in {1, 50, 87}:  # unsupported info class/filesystem
                raise ctypes.WinError(error)
    elif sys.platform == 'linux':
        import array
        import ctypes
        import errno
        import fcntl
        from .secure_paths import open_directory
        flags = array.array('l', [0])
        # Linux FS_IOC_GETFLAGS = _IOR('f', 1, long).
        request = 0x80000000 | (flags.itemsize << 16) | (ord('f') << 8) | 1
        with open_directory(path) as descriptor:
            # A zero CASEFOLD flag does not describe mount-level nocase on
            # NTFS/CIFS/etc. Only use it for filesystems with known semantics.
            filesystem = (ctypes.c_long * 32)()
            function = ctypes.CDLL(None, use_errno=True).fstatfs
            function.argtypes = [ctypes.c_int, ctypes.c_void_p]
            function.restype = ctypes.c_int
            if function(descriptor, filesystem) != 0:
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number), str(path))
            kind = filesystem[0] & 0xffffffff
            if kind in {0x01021994, 0x858458f6, 0x58465342, 0x9123683e}:
                return True  # tmpfs, ramfs, XFS, Btrfs
            try:
                if kind in {0xef53, 0xf2f52010, 0x794c7630}:  # ext*, F2FS, overlay
                    fcntl.ioctl(descriptor, request, flags)
                    return not bool(flags[0] & 0x40000000)  # FS_CASEFOLD_FL
            except OSError as exc:
                if exc.errno not in {errno.ENOTTY, errno.EOPNOTSUPP, errno.EINVAL}:
                    raise

    raise OSError('Cannot determine filesystem case sensitivity for directory: ' + str(path))


def assert_missing_location(path: Path) -> None:
    """Reject occupied entries and aliased ancestors using the common rule."""
    path = lexical_path(path)
    for candidate in reversed((path, *path.parents)):
        if str(candidate) == str(candidate.parent) or not filesystem_path(candidate.parent).is_dir():
            continue
        sensitive = _directory_case_sensitive(candidate.parent)
        key = candidate.name if sensitive else candidate.name.casefold()
        for entry in filesystem_path(candidate.parent).iterdir():
            name = entry.name if sensitive else entry.name.casefold()
            if (name == key
                    and (str(candidate) == str(path) or entry.name != candidate.name)):
                raise FileExistsError('Directory location already exists: ' + str(candidate))


def contains(root: Path, path: Path) -> bool:
    return DirectoryComparison().contains(root, path)


class DirectoryComparison:
    """Case evidence for one path-decision batch, never across mutations.

    Owners create this after taking their decision lock and discard it before
    filesystem work. A subsequent decision (even in the same transaction)
    must create another instance to observe mount/flag/alias changes.
    """

    def __init__(self):
        self._policies = {}

    def _case_sensitive(self, parent):
        if parent not in self._policies:
            self._policies[parent] = _directory_case_sensitive(parent)
        return self._policies[parent]

    def contains(self, root: Path, path: Path) -> bool:
        return _contains(root, path, self._case_sensitive)

    def case_sensitive_prefix(self, path: Path) -> Path:
        """An exact lexical prefix shared by every possible native case alias.

        This only narrows candidates, never decides membership. Stop before
        the first uncertain/insensitive component; contains() still checks
        candidates and propagates unknown-policy errors for possible matches.
        """
        path = lexical_path(path)
        prefix = Path(path.anchor)
        for part in path.parts[1:]:
            try:
                if not self._case_sensitive(prefix):
                    break
            except OSError:
                break
            prefix /= part
        return prefix

    def overlaps(self, left: Path, right: Path) -> bool:
        return self.contains(left, right) or self.contains(right, left)


def _contains(root, path, case_sensitive):
    root, path = lexical_path(root), lexical_path(path)
    # Unrelated names cannot become ancestors under either native case rule.
    # Reject them before probing every parent directory. Potential matches
    # still use fresh filesystem evidence; this is not a case-policy cache.
    if len(root.parts) > len(path.parts) or any(
            parent.casefold() != child.casefold()
            for parent, child in zip(root.parts, path.parts)):
        return False
    parent, child = _location_key(root, case_sensitive), _location_key(path, case_sensitive)
    return child[:len(parent)] == parent


def overlaps(left: Path, right: Path) -> bool:
    return DirectoryComparison().overlaps(left, right)


def rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing even an empty target."""
    assert_missing_location(destination)
    if os.name == 'nt':
        os.rename(filesystem_path(source), filesystem_path(destination))  # Windows rename refuses existing targets.
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'linux':
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        arguments = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == 'darwin':
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise NotImplementedError('Atomic directory creation is not supported on this platform')
    function.restype = ctypes.c_int
    if function(*arguments) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(destination))


def validate_filename(path: Path) -> None:
    lexical_path(path)
    if os.name == 'nt':
        devices = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
        for name in path.parts[1:]:
            if (name[-1:] in {' ', '.'} or name.split('.')[0].upper() in devices
                    or any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name)):
                raise ValueError('Directory name is not valid on Windows: ' + name)

"""Host-owned subscription directories and instance-owned Provider control state."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re

from .file_lock import try_lock_descriptor

PROVIDERS = ("codex", "claude", "kimi", "antigravity")


def host_user_home() -> Path:
    """OS identity, independent of the service HOME.

    Tests and installers may explicitly inject PORTAL_ACCOUNT_HOST_HOME. It is
    never persisted as an instance data root or inherited by Provider children.
    """
    override = os.environ.get("PORTAL_ACCOUNT_HOST_HOME")
    if override:
        return canonical_directory(override)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        # CSIDL_PROFILE; unlike HOME this is owned by the Windows user profile.
        buffer = ctypes.create_unicode_buffer(32768)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.SHGetFolderPathW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                                         wintypes.DWORD, wintypes.LPWSTR]
        if shell.SHGetFolderPathW(None, 0x28, None, 0, buffer):
            raise OSError("Cannot resolve the Windows user profile")
        return Path(buffer.value).resolve()
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def canonical_directory(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value) or any(c in str(value) for c in "\0\r\n"):
        raise ValueError("Account directory must be a canonical absolute path")
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or str(path) != str(value):
        raise ValueError("Account directory must be canonical and must not contain symbolic links")
    if path.exists() and not path.is_dir():
        raise ValueError("Account directory is not a directory")
    return path


def validate_identity(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError("Invalid account or secret identifier")
    return value


def provider_root(provider: str) -> Path:
    if provider not in PROVIDERS:
        raise ValueError("Provider has no subscription directory")
    return host_user_home() / f".{provider}-accounts"


def default_directory(provider: str, account_id: str) -> Path:
    name = validate_identity(account_id).removeprefix(provider + "_")
    return provider_root(provider) / validate_identity(name)


def runtime_root() -> Path:
    from . import portal_paths
    from .instance_config import installed_root
    root = installed_root(portal_paths.CONFIG_PATH)
    return (root / "runtime" if root else portal_paths.path("instances_root") / "runtime") / "provider-accounts"


def account_runtime(provider: str, account_id: str) -> Path:
    return runtime_root() / validate_identity(provider) / validate_identity(account_id)


def directory_runtime(directory: Path) -> Path:
    """Control state for a directory when no registration ID is in scope."""
    return runtime_root() / "directories" / hashlib.sha256(os.fsencode(canonical_directory(directory))).hexdigest()


@contextmanager
def daemon_lock(directory: Path):
    """One Portal-managed daemon per canonical directory, across instances."""
    directory = canonical_directory(directory)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    lock = directory.parent / ("." + directory.name + ".portal-daemon.lock")
    if lock.is_symlink():
        raise ValueError("Provider daemon lock must not be a symbolic link")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a+b") as stream:
        if not try_lock_descriptor(stream.fileno()):
            raise RuntimeError(f"Provider account directory is already in use by a Portal daemon: {directory}")
        yield

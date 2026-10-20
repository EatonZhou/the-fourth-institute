from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from management_common.file_lock import lock_stream
from management_common.file_durability import sync_directory


def _prepare_permissions(descriptor: int, temporary: Path, target: Path, mode: int) -> None:
    if os.name == "nt":
        from management_common.file_permissions import prepare_atomic_permissions

        prepare_atomic_permissions(temporary, target, mode)
    else:
        os.fchmod(descriptor, mode)
        if mode & 0o077 == 0:
            from management_common.file_permissions import assert_private_posix_acl

            assert_private_posix_acl(descriptor)


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize writes to business state shared by Portal instances."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as stream:
        with lock_stream(stream):
            yield


def atomic_replace_text(path: Path, content: str, *, mode: int | None = None) -> None:
    """Replace shared text state without exposing a partial write."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target_mode = mode if mode is not None else (target.stat().st_mode & 0o7777 if target.exists() else 0o664)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _prepare_permissions(stream.fileno(), temporary, target, target_mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_replace_json(
    path: Path,
    payload: object,
    *,
    mode: int | None = None,
    indent: int | None = 2,
    sort_keys: bool = True,
) -> None:
    """Serialize JSON and durably replace its target with one canonical policy.

    A caller may require an explicit permission mode for sensitive state.  Other
    callers preserve the existing target mode and use the shared-state default
    for a new file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target_mode = (
        int(mode)
        if mode is not None
        else (target.stat().st_mode & 0o7777 if target.exists() else 0o664)
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _prepare_permissions(stream.fileno(), temporary, target, target_mode)
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=indent,
                sort_keys=sort_keys,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_create_json(
    path: Path,
    payload: object,
    *,
    mode: int = 0o664,
    indent: int | None = 2,
    sort_keys: bool = True,
) -> None:
    """Durably create immutable JSON state and fail if the target exists."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, int(mode))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if os.name == "nt":
                from management_common.file_permissions import set_created_file_permissions

                set_created_file_permissions(target, int(mode))
            else:
                _prepare_permissions(stream.fileno(), target, target, int(mode))
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=indent,
                sort_keys=sort_keys,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(target.parent)
    except Exception:
        target.unlink(missing_ok=True)
        raise

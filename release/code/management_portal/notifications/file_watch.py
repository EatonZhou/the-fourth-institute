from __future__ import annotations

import ctypes
import os
import select
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class RecursiveFileChangeWatcher:
    """Small Linux inotify adapter shared by local projection producers."""

    _EVENT = struct.Struct("iIII")
    _IN_CLOSE_WRITE = 0x00000008
    _IN_MOVED_FROM = 0x00000040
    _IN_MOVED_TO = 0x00000080
    _IN_CREATE = 0x00000100
    _IN_DELETE = 0x00000200
    _IN_DELETE_SELF = 0x00000400
    _IN_MOVE_SELF = 0x00000800
    _IN_Q_OVERFLOW = 0x00004000
    _IN_IGNORED = 0x00008000
    _IN_ISDIR = 0x40000000
    _WATCH_MASK = (
        _IN_CLOSE_WRITE
        | _IN_MOVED_FROM
        | _IN_MOVED_TO
        | _IN_CREATE
        | _IN_DELETE
        | _IN_DELETE_SELF
        | _IN_MOVE_SELF
    )

    def __init__(
        self,
        directories: Iterable[Path],
        on_change: Callable[[str], None],
        on_overflow: Callable[[], None],
        *,
        thread_name: str = "portal-filesystem-projection",
        recursive: bool = True,
        track_new_directories: bool | None = None,
    ) -> None:
        self._configured_directories = {
            Path(path).expanduser().resolve(strict=False)
            for path in directories
            if Path(path).expanduser().is_dir()
        }
        self.on_change = on_change
        self.on_overflow = on_overflow
        self.recursive = bool(recursive)
        self.track_new_directories = (
            self.recursive
            if track_new_directories is None
            else bool(track_new_directories)
        )
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._fd = -1
        self._paths_by_watch: dict[int, Path] = {}
        self._watch_by_path: dict[Path, int] = {}
        self._add_watch: object | None = None
        self._ready = False
        self._last_event_at = ""
        self._last_error = ""
        self._thread = threading.Thread(
            target=self._run,
            name=str(thread_name or "portal-filesystem-projection"),
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        fd = self._fd
        self._fd = -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        self._thread.join(timeout=2)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": True,
                "recursive": self.recursive,
                "track_new_directories": self.track_new_directories,
                "ready": self._ready,
                "watch_count": len(self._paths_by_watch),
                "last_event_at": self._last_event_at,
                "last_error": self._last_error,
            }

    def _set_error(self, error: object) -> None:
        with self._lock:
            self._last_error = str(error or "").strip()[:1000]

    def add_directories(
        self,
        directories: Iterable[Path],
        *,
        recursive: bool = False,
    ) -> None:
        resolved = {
            Path(path).expanduser().resolve(strict=False)
            for path in directories
            if Path(path).expanduser().is_dir()
        }
        with self._lock:
            self._configured_directories.update(resolved)
            add_watch = self._add_watch
            active = self._fd >= 0
        if not active or add_watch is None:
            return
        for path in sorted(resolved, key=str):
            try:
                if recursive:
                    self._add_tree(path, add_watch)
                else:
                    self._add_directory(path, add_watch)
            except OSError as exc:
                self._set_error(exc)

    def _add_directory(self, path: Path, add_watch: object) -> None:
        resolved = path.expanduser().resolve(strict=False)
        if not resolved.is_dir():
            return
        with self._lock:
            if resolved in self._watch_by_path:
                return
        watch = int(add_watch(self._fd, os.fsencode(resolved), self._WATCH_MASK))
        if watch < 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), str(resolved))
        with self._lock:
            self._paths_by_watch[watch] = resolved
            self._watch_by_path[resolved] = watch

    def _add_tree(self, root: Path, add_watch: object) -> None:
        self._add_directory(root, add_watch)
        try:
            descendants = root.rglob("*")
            for path in descendants:
                if (
                    path.is_dir()
                    and not path.name.startswith(".")
                    and "__pycache__" not in path.parts
                ):
                    self._add_directory(path, add_watch)
        except OSError as exc:
            self._set_error(exc)

    def _run(self) -> None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            init = libc.inotify_init1
            init.argtypes = [ctypes.c_int]
            init.restype = ctypes.c_int
            add_watch = libc.inotify_add_watch
            add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            add_watch.restype = ctypes.c_int
            fd = int(init(os.O_NONBLOCK | os.O_CLOEXEC))
            if fd < 0:
                error_number = ctypes.get_errno()
                raise OSError(error_number, os.strerror(error_number))
            self._fd = fd
            with self._lock:
                self._add_watch = add_watch
                directories = tuple(
                    sorted(self._configured_directories, key=str)
                )
            for directory in directories:
                try:
                    if self.recursive:
                        self._add_tree(directory, add_watch)
                    else:
                        self._add_directory(directory, add_watch)
                except OSError as exc:
                    self._set_error(exc)
            with self._lock:
                self._ready = True

            while not self._stopped.is_set():
                try:
                    readable, _writable, _errors = select.select([fd], [], [], 1.0)
                except (OSError, ValueError):
                    if self._stopped.is_set():
                        break
                    raise
                if not readable:
                    continue
                try:
                    payload = os.read(fd, 256 * 1024)
                except BlockingIOError:
                    continue
                offset = 0
                while offset + self._EVENT.size <= len(payload):
                    watch, mask, _cookie, name_length = self._EVENT.unpack_from(
                        payload,
                        offset,
                    )
                    offset += self._EVENT.size
                    raw_name = payload[offset : offset + name_length]
                    offset += name_length
                    if mask & self._IN_Q_OVERFLOW:
                        self.on_overflow()
                        continue
                    with self._lock:
                        directory = self._paths_by_watch.get(watch)
                    if directory is None:
                        continue
                    name = (
                        os.fsdecode(raw_name.split(b"\0", 1)[0])
                        if raw_name
                        else ""
                    )
                    path = (
                        (directory / name).resolve(strict=False)
                        if name
                        else directory
                    )
                    if mask & self._IN_IGNORED:
                        with self._lock:
                            removed = self._paths_by_watch.pop(watch, None)
                            if removed is not None:
                                self._watch_by_path.pop(removed, None)
                        continue
                    if mask & self._IN_ISDIR and mask & (
                        self._IN_CREATE | self._IN_MOVED_TO
                    ):
                        if self.track_new_directories:
                            self._add_tree(path, add_watch)
                    with self._lock:
                        self._last_event_at = _now_iso()
                    self.on_change(str(path))
        except Exception as exc:
            if not self._stopped.is_set():
                self._set_error(exc)
        finally:
            with self._lock:
                self._ready = False
                self._add_watch = None
            fd = self._fd
            self._fd = -1
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

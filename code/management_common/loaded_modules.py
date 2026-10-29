"""Which Portal modules the running shared services have imported.

A publish restarts a shared service, or reloads the Worker pool, when it
changes a source file that one of the service's processes has imported. A
module no process has imported yet is read from the new source when first
needed. The services import many modules lazily, long after start, so every
process keeps a record that grows with each first import of a Portal module:
``<install root>/runtime/loaded-modules/<component>.<pid>``, one module name
per line. Worker children inherit the component from their manager.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from management_common.process_runtime import process_alive

PACKAGES = frozenset({"management_portal", "management_common", "knowledge_board_lan"})
DIRECTORY_ENV = "PORTAL_LOADED_MODULES_DIR"
COMPONENT_ENV = "PORTAL_LOADED_MODULES_COMPONENT"


def directory(install_root: Path) -> Path:
    return Path(install_root) / "runtime" / "loaded-modules"


def module_name(path: str) -> str | None:
    """The module a repository source path defines; None for other files."""
    if not path.endswith(".py") or path.partition("/")[0] not in PACKAGES:
        return None
    return path[:-3].replace("/", ".").removesuffix(".__init__")


def _portal(name: str) -> bool:
    return name.partition(".")[0] in PACKAGES


class _Recorder:
    """First on sys.meta_path: notes a Portal import, then lets the real finders load it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._names: set[str] = set()
        self._lock = threading.Lock()
        self._broken = False

    def note(self, names) -> None:
        with self._lock:
            fresh = sorted(name for name in set(names) - self._names if _portal(name))
            if not fresh or self._broken:
                return
            try:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write("".join(name + "\n" for name in fresh))
            except OSError:
                # An incomplete record would let a publish skip this process.
                # Without one the publish restarts its service for any change.
                self._broken = True
                self._path.unlink(missing_ok=True)
                return
            self._names.update(fresh)

    def find_spec(self, name, path=None, target=None):
        if _portal(name):
            self.note((name,))
        return None


def _live_records(folder: Path):
    for record in folder.glob("*.*"):
        component, _, pid = record.name.rpartition(".")
        if not component or not pid.isdigit():
            continue
        if process_alive(int(pid)):
            yield component, record
        else:
            record.unlink(missing_ok=True)


def track() -> None:
    """Start this process's record when its service unit names a component."""
    folder, component = os.environ.get(DIRECTORY_ENV), os.environ.get(COMPONENT_ENV)
    if not folder or not component or any(isinstance(finder, _Recorder) for finder in sys.meta_path):
        return
    path = Path(folder) / f"{component}.{os.getpid()}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for _ in _live_records(path.parent):
            pass
        path.write_text("", encoding="utf-8")
    except OSError:
        return
    recorder = _Recorder(path)
    # Hook first, then copy what is already loaded, so no concurrent import is missed.
    sys.meta_path.insert(0, recorder)
    recorder.note(list(sys.modules))


def loaded(folder: Path) -> dict[str, set[str]]:
    """Module names per component from the records of processes still running."""
    result: dict[str, set[str]] = {}
    if not folder.is_dir():
        return result
    for component, record in _live_records(folder):
        try:
            names = record.read_text(encoding="utf-8").split()
        except OSError:
            continue
        result.setdefault(component, set()).update(names)
    return result

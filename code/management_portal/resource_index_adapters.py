"""The resource index adapter contract and what path-based adapters share: path
resolution, source and code fingerprints, and the directories they watch."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Protocol

import knowledge_board_lan.knowledge_source as knowledge_source
import skill_board_lan.skill_backend as skill_backend

from management_common.scan_cancellation import check_scan_cancelled, walk_paths
from management_common.text_values import stripped_text
from management_portal.resources.registry_store import get_registry_store


# Change routing asks every adapter whether it owns the changed path.
# Resolving a path makes one system call per component and each call gives up
# the interpreter lock; while other ResourceService threads compute, every
# reacquisition waits its turn, and the ~190 calls of one routing took
# 0.3-1.5 s.  The changed path is therefore resolved once per routing, and
# the adapters' configured roots, which rarely move, reuse their resolution
# for a short while.
ROOT_RESOLUTION_SECONDS = 30.0
_resolved_roots: dict[str, tuple[float, Path]] = {}
_routing_paths: ContextVar[dict[str, Path] | None] = ContextVar(
    "resource_routing_paths", default=None
)
# Real paths of the indexed sources, resolved by the change finder's child
# process, while an adapter applies the changes it found.
_known_paths: ContextVar[dict[str, Path] | None] = ContextVar(
    "resource_known_paths", default=None
)


def _resolved_root(root: Path | str) -> Path:
    key = str(root)
    now = time.monotonic()
    cached = _resolved_roots.get(key)
    if cached is not None and now - cached[0] < ROOT_RESOLUTION_SECONDS:
        return cached[1]
    resolved = Path(key).expanduser().resolve(strict=False)
    _resolved_roots[key] = (now, resolved)
    return resolved


def _resolved_path(path: Path) -> Path:
    for known in (_routing_paths.get(), _known_paths.get()):
        if known is not None and (resolved := known.get(str(path))) is not None:
            return resolved
    return path.expanduser().resolve(strict=False)


def _known_path(raw_path: str | Path) -> Path:
    return _resolved_path(Path(raw_path))


class ResourceAdapter(Protocol):
    """A projection of one domain's sources.

    An adapter that also has ``source_scope()`` and ``scan_changes()`` is
    checked by the sources that changed (AdapterLayer._refresh_changed_sources)
    and its ``fingerprint()`` is not called.
    """

    name: str
    resource_types: frozenset[str]

    def fingerprint(self) -> str: ...

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...

    def owns_path(self, path: Path) -> bool: ...


def _path_fingerprint(paths: Iterable[Path], *, recursive: bool = False) -> str:
    digest = hashlib.sha256()
    seen: set[str] = set()
    candidates: list[Path] = []
    for source in paths:
        check_scan_cancelled()
        path = Path(source).expanduser()
        key = str(path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(path)
        if recursive and path.is_dir():
            try:
                candidates.extend(sorted(walk_paths(path), key=lambda item: str(item)))
            except OSError:
                pass
    for path in candidates:
        check_scan_cancelled()
        try:
            stat = path.stat()
            marker = f"{path.resolve(strict=False)}\0{stat.st_mode}\0{stat.st_size}\0{stat.st_mtime_ns}\n"
        except OSError:
            marker = f"{path.resolve(strict=False)}\0missing\n"
        digest.update(marker.encode("utf-8", errors="surrogateescape"))
    return digest.hexdigest()


# The index code that projects and stores the records.  It was one module,
# whose own source this fingerprint read; a change to any of it still
# invalidates every adapter's projection.
_INDEX_CODE_MODULES = (
    "resource_index_backend.py",
    "resource_index_records.py",
    "resource_index_store.py",
    "resource_index_adapters.py",
    "resource_index_refresh.py",
    "resource_index_knowledge.py",
    "resource_index_registries.py",
)


def _current_projection_code_fingerprint() -> str:
    digest = hashlib.sha256()
    # Parsers determine persisted titles and summaries just as the adapters
    # do. Their changes must invalidate unchanged files' old projections.
    for source in (
        *(Path(__file__).with_name(name) for name in _INDEX_CODE_MODULES),
        knowledge_source.__file__,
        skill_backend.__file__,
    ):
        digest.update(Path(source).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


_CURRENT_PROJECTION_CODE_FINGERPRINT = _current_projection_code_fingerprint()


def _adapter_projection_code_fingerprint(adapter: object) -> str:
    """Fingerprint the code loaded by this process, not future edits on disk."""
    module = sys.modules[type(adapter).__module__]
    feature = getattr(module, "PROJECTION_CODE_FINGERPRINT", "")
    if not feature:
        return _CURRENT_PROJECTION_CODE_FINGERPRINT
    return hashlib.sha256(
        (_CURRENT_PROJECTION_CODE_FINGERPRINT + "\0" + feature).encode()
    ).hexdigest()


def _tree_paths(
    roots: Iterable[Path],
    *,
    suffixes: set[str] | None = None,
    include_hidden: bool = False,
) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        check_scan_cancelled()
        if not root.exists():
            paths.append(root)
            continue
        paths.append(root)
        try:
            descendants = walk_paths(root) if root.is_dir() else ()
            for path in descendants:
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    continue
                if any(
                    part == "__pycache__"
                    or (part.startswith(".") and not include_hidden)
                    for part in relative.parts
                ):
                    continue
                if path.is_dir() or suffixes is None or path.suffix.lower() in suffixes:
                    paths.append(path)
        except OSError:
            continue
    return paths


def _watch_directories(paths: Iterable[Path]) -> list[Path]:
    return sorted(
        {
            Path(path).expanduser().resolve(strict=False)
            for path in paths
            if Path(path).expanduser().is_dir()
        },
        key=str,
    )


def _watch_tree(root: Path, *, max_depth: int) -> list[Path]:
    resolved = root.expanduser().resolve(strict=False)
    if not resolved.is_dir():
        return []
    directories: list[Path] = []
    for current, names, _files in os.walk(resolved):
        check_scan_cancelled()
        path = Path(current)
        depth = len(path.relative_to(resolved).parts)
        names[:] = sorted(
            name
            for name in names
            if not name.startswith(".") and name != "__pycache__"
        )
        directories.append(path)
        if depth >= max(0, int(max_depth)):
            names[:] = []
    return directories


class _PathsAdapter:
    name = ""
    resource_types: frozenset[str] = frozenset()
    roots: tuple[Path, ...] = ()
    # Resource registry collections this projection reads; their writes are
    # routed here and their revisions are part of the fingerprint.
    registry_collections: frozenset[str] = frozenset()
    recursive_fingerprint = False
    protect_existing_sources = False

    def _registry_fingerprint(self) -> str:
        revisions = get_registry_store().revisions(self.registry_collections)
        return json.dumps(revisions, sort_keys=True)

    def source_paths(self) -> list[Path]:
        return list(self.roots)

    def _fingerprint_paths(
        self,
        paths: Iterable[Path],
        *,
        recursive: bool = False,
    ) -> str:
        source_fingerprint = _path_fingerprint(paths, recursive=recursive)
        digest = hashlib.sha256()
        digest.update(_adapter_projection_code_fingerprint(self).encode("ascii"))
        digest.update(b"\0")
        digest.update(source_fingerprint.encode("ascii"))
        if self.registry_collections:
            digest.update(b"\0")
            digest.update(self._registry_fingerprint().encode("ascii"))
        return digest.hexdigest()

    def fingerprint(self) -> str:
        return self._fingerprint_paths(
            self.source_paths(),
            recursive=self.recursive_fingerprint,
        )

    def owns_path(self, path: Path) -> bool:
        resolved = _resolved_path(path)
        for root in self.roots:
            candidate = _resolved_root(root)
            if resolved == candidate or candidate in resolved.parents:
                return True
        return False

    def watch_directories(self) -> list[Path]:
        directories: set[Path] = set()
        for source in self.roots:
            path = source.expanduser().resolve(strict=False)
            if path.is_dir():
                directories.add(path)
            elif path.parent.is_dir():
                directories.add(path.parent)
        return sorted(directories, key=str)

    def recover_snapshot(
        self,
        previous: Iterable[dict[str, Any]],
        current: Iterable[dict[str, Any]],
        source_state: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        current_records = list(current)
        if not self.protect_existing_sources:
            return current_records, source_state
        current_ids = {stripped_text(item.get("resource_id")) for item in current_records}
        current_source_paths = {
            Path(stripped_text((item.get("source") or {}).get("path")))
            .expanduser()
            .resolve(strict=False)
            for item in current_records
            if stripped_text((item.get("source") or {}).get("path"))
        }
        preserved: list[dict[str, Any]] = []
        resource_errors = [
            dict(item)
            for item in source_state.get("resource_errors") or []
            if isinstance(item, dict)
        ]
        for record in previous:
            resource_id = stripped_text(record.get("resource_id"))
            source = record.get("source") if isinstance(record.get("source"), dict) else {}
            source_path = Path(stripped_text(source.get("path"))).expanduser().resolve(strict=False)
            if (
                resource_id
                and resource_id not in current_ids
                and source_path.is_file()
                and source_path not in current_source_paths
                and self.protects_missing_source(record, source_path)
            ):
                preserved.append(record)
                resource_errors.append(
                    {
                        "resource_id": resource_id,
                        "path": str(source_path),
                        "error": "resource source still exists but this scan could not parse it",
                    }
                )
        return [
            *current_records,
            *preserved,
        ], {
            **source_state,
            "resource_error_count": len(resource_errors),
            "resource_errors": resource_errors[:100],
        }

    def protects_missing_source(
        self,
        _record: dict[str, Any],
        _source_path: Path,
    ) -> bool:
        """Whether an omitted existing source indicates an unsafe parse failure."""
        return True

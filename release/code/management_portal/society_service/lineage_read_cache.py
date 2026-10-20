"""Short lived Lineage read projections, keyed by storage root and actor."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
import json
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable

from . import lineage_state_domains, resident_catalog, resident_protocol

CacheKey = tuple[str, str, str]


def cache_key(root: Path, lineage_id: str, actor: str) -> CacheKey:
    return str(root.resolve(strict=False)), lineage_id, actor


def definition_refresh_signature(lineage: dict[str, Any]) -> str:
    """Hash the authored graph and layout without volatile runtime projections."""

    def stable(value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): stable(item)
                for key, item in value.items()
                if str(key) not in {"checked_at", "refresh_signature"}
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    definition = lineage_state_domains.definition_projection(lineage)
    for field in {
        "program_invoke_wire_runtime",
        "program_event_source_runtime",
        "program_effect_runtime",
        "program_execution_explanations",
        "refresh_signature",
    }:
        definition.pop(field, None)
    encoded = json.dumps(
        stable(definition),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class LineageReadCache:
    def __init__(self, *, ttl_seconds: float = 5.0, max_entries: int = 128) -> None:
        self._lock = threading.RLock()
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._build_locks = weakref.WeakValueDictionary()
        self._records: dict[str, dict[CacheKey, dict[str, Any]]] = {
            "state": {}, "view": {}, "message_counts": {},
        }

    def build_lock(self, kind: str, key: CacheKey) -> threading.RLock:
        # Only equal reads share a calculation lock. The cache-wide lock never
        # covers storage/RPC work; unused calculation locks leave no entries.
        with self._lock:
            return self._build_locks.setdefault((kind, key), threading.RLock())

    def get(self, kind: str, key: CacheKey, fingerprint: object) -> dict[str, Any] | None:
        with self._lock:
            record = self._records[kind].get(key)
            if not record or record["expires_at"] <= time.monotonic() or record.get("fingerprint") != fingerprint:
                return None
            return record

    def put(self, kind: str, key: CacheKey, value: dict[str, Any]) -> None:
        with self._lock:
            cache = self._records[kind]
            if len(cache) >= self._max_entries:
                oldest = sorted(cache, key=lambda item: float(cache[item].get("expires_at") or 0))[
                    : max(1, self._max_entries // 4)
                ]
                for item in oldest:
                    cache.pop(item, None)
            cache[key] = {**value, "expires_at": time.monotonic() + self._ttl_seconds}

    def invalidate(self, lineage_id: str) -> None:
        with self._lock:
            for cache in self._records.values():
                for key in [key for key in cache if key[1] == lineage_id]:
                    cache.pop(key, None)

    def clear(self, kind: str) -> None:
        with self._lock:
            self._records[kind].clear()


def definition_fingerprint(
    lineage: dict[str, Any],
    *,
    seed_folder: Callable[[str], Path],
    node_resident_id: Callable[..., str],
) -> tuple[tuple[str, int, int], ...]:
    lineage_id = str(lineage.get("lineage_id") or "")
    canonical_version = ":".join(
        str(lineage.get(field) or 0)
        for field in ("revision", "layout_revision", "runtime_revision")
    )
    fingerprint: list[tuple[str, int, int]] = [
        (f"lineage:{lineage_id}:{canonical_version}", 0, 0)
    ]
    # Invocation history changes independently of relation-work revisions.
    observations = [(node.get("id"), node.get("status"), node.get("last_execution"))
                    for node in lineage.get("nodes") or [] if isinstance(node, dict)]
    digest = hashlib.sha256(json.dumps(observations, sort_keys=True).encode()).hexdigest()
    fingerprint.append((f"resident-observations:{digest}", 0, 0))
    paths: list[Path] = []
    for component in (lineage.get("program") or {}).get("components") or []:
        if not isinstance(component, dict) or str(component.get("kind") or "") != "seed":
            continue
        rel_path = str((component.get("config") or {}).get("artifact_path") or "").strip()
        if not rel_path:
            continue
        try:
            folder = seed_folder(lineage_id)
        except ValueError:
            continue
        candidate = (folder / rel_path).resolve(strict=False)
        try:
            candidate.relative_to(folder)
        except ValueError:
            continue
        paths.append(candidate)
    for path in paths:
        try:
            stat = path.stat()
            fingerprint.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            fingerprint.append((str(path), 0, 0))
    state_snapshots = resident_protocol.read_resident_state_snapshots([
        node_resident_id(node)
        for node in lineage.get("nodes") or []
        if isinstance(node, dict) and node_resident_id(node)
    ])
    fingerprint.extend(
        (f"resident-state:{resident_id}", int(snapshot.get("cursor") or 0), 0)
        for resident_id, snapshot in sorted(state_snapshots.items())
    )
    return tuple(fingerprint)


@dataclass(frozen=True)
class ReadProjectionPorts:
    safe_actor: Callable[[str], str]
    snapshot_for_read: Callable[[str, str], dict[str, Any]]
    cache_key: Callable[[str, str], CacheKey]
    fingerprint: Callable[[dict[str, Any]], object]
    cache: LineageReadCache
    clone_json: Callable[[dict[str, Any]], dict[str, Any]]
    project_titles: Callable[[dict[str, Any]], None]
    verified_view: Callable[[dict[str, Any], str], dict[str, Any]]


def current_lineage_view_for_read(
    lineage_id: str,
    actor: str,
    *,
    ports: ReadProjectionPorts,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reuse projections only while their complete live-fact fingerprint matches."""
    actor_id = ports.safe_actor(actor)
    lineage = ports.snapshot_for_read(lineage_id, actor_id)
    cache_key = ports.cache_key(lineage_id, actor_id)
    fingerprint = ports.fingerprint(lineage)
    cached = ports.cache.get("state", cache_key, fingerprint)
    if cached:
        lineage = ports.clone_json(cached["lineage"])
    else:
        ports.project_titles(lineage)
        ports.cache.put("state", cache_key, {
            "fingerprint": fingerprint, "lineage": ports.clone_json(lineage),
        })
    cached = ports.cache.get("view", cache_key, fingerprint)
    if cached:
        return lineage, ports.clone_json(cached["view"])
    view = ports.verified_view(lineage, actor_id)
    ports.cache.put("view", cache_key, {"fingerprint": fingerprint, "view": ports.clone_json(view)})
    return lineage, view

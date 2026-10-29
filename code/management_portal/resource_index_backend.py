from __future__ import annotations
from management_common.portal_paths import path as portal_path

import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import quote

import knowledge_board_lan.knowledge_backend as knowledge_backend
import skill_board_lan.skill_backend as skill_backend

from management_common.json_columns import json_text, json_value
from management_common.resource_identity import get_resource_type_registry
from management_common.portal_database import portal_database
from management_common.database_time import iso_time
from management_common.scan_cancellation import (
    ScanCancelled, cancellable_scan, check_scan_cancelled, walk_paths,
)
from management_portal.portal_access_control import (
    PortalAccessStore,
    get_portal_access_store,
)
from management_portal.resources import timing
from management_common.format_tags import schema_matches
from management_common.text_values import stripped_text
from management_portal.resources.contracts import ResourceQuery
from management_portal.resources.index import ResourceRecordsProjector
from management_portal.resources.index_runtime import ResourceIndexRuntime
from management_portal import project_registry, semantic_search_store, portal_features
from management_portal.resources.providers import project as project_provider
from management_portal.resources.query import (
    filter_query_candidates,
    prefilter_query_candidates,
    query_records,
    readable_flags,
    score_id_title,
)
from management_portal.resources.portal import portal_url_for_resource
from management_portal.resources.schema import (
    RESOURCE_INDEX_SCHEMA,
    normalize_resource_attributes,
    resource_relation,
)
from management_portal.resources.config import resource_database_schema
from management_portal.resources.timing import ReportingLock
from management_portal.resources.registry_store import get_registry_store, parse_registry_change_key
from management_portal.resources.collaboration_registry import (
    descriptor_resource_record,
    CollaborationDescriptorStore,
)
from management_portal.notifications import (
    get_notification_publisher,
    resource_projection_subject,
)
from management_portal.notifications.file_watch import RecursiveFileChangeWatcher

from . import (
    device_identity_backend,
    resource_identity_backend,
    search_identity_backend,
)


SCHEMA = RESOURCE_INDEX_SCHEMA
RESOURCE_INDEX_STORE_SCHEMA = "tfi.resource-index-store.v1"
RESOURCE_RECONCILE_SECONDS = max(
    300,
    int(
        os.getenv("PORTAL_RESOURCE_INDEX_RECONCILE_SECONDS", "300")
        or "300"
    ),
)
WATCH_FILESYSTEM = os.getenv("PORTAL_RESOURCE_INDEX_WATCH", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
RESOURCE_IDENTITY = get_resource_type_registry()
LOGGER = logging.getLogger(__name__)


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_iso() -> str:
    return _utc_iso(datetime.now(timezone.utc))


def _dedupe(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = stripped_text(value)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _timestamp_iso(value: object) -> str:
    clean = stripped_text(value)
    if not clean:
        return ""
    try:
        return _utc_iso(datetime.fromtimestamp(float(clean), timezone.utc))
    except (TypeError, ValueError, OSError, OverflowError):
        return clean


def _path_modified_at(path: str | Path, fallback: object = "") -> str:
    try:
        return _utc_iso(datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc))
    except (OSError, ValueError):
        return _timestamp_iso(fallback)


def _validate_resource_id(resource_id: str, resource_type: str) -> None:
    RESOURCE_IDENTITY.validate(resource_id, resource_type)


# A query slower than this reports its step timings (see resources/timing.py).
SLOW_QUERY_SECONDS = 0.3
SLOW_REFRESH_SECONDS = 5.0
# A change is looked for from this long before the previous check started, so
# a file written just as that check began is not missed.
SOURCE_CHANGE_MARGIN_SECONDS = 2.0
SOURCE_CHANGE_TIMEOUT_SECONDS = 900.0
_SOURCE_CHANGE_FINDER = Path(__file__).with_name("resource_source_changes.py")


def _find_source_changes(request: dict[str, Any]) -> dict[str, Any]:
    """Run resource_source_changes.py on ``request`` in a child process."""
    check_scan_cancelled()
    command = [sys.executable, "-I", str(_SOURCE_CHANGE_FINDER)]
    # The request lists every indexed source (4,000 papers is about 300 KB),
    # more than a pipe holds. communicate() writes input only on its first
    # call, so a child that started reading after that call's 0.1 s never got
    # the rest and the scan hung until the timeout. A file needs no writer.
    with tempfile.TemporaryFile() as request_file:
        request_file.write(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        request_file.seek(0)
        with subprocess.Popen(
            command, stdin=request_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            deadline = time.monotonic() + SOURCE_CHANGE_TIMEOUT_SECONDS
            try:
                while True:
                    check_scan_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, SOURCE_CHANGE_TIMEOUT_SECONDS)
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                process.kill()
                process.communicate()
                raise
    check_scan_cancelled()
    if process.returncode != 0:
        raise RuntimeError(
            "resource source change finder failed: "
            + stderr.decode("utf-8", errors="replace")[-1000:]
        )
    return json.loads(stdout)


def _json_value(value: object) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"resource index value is not JSON serializable: {exc}") from exc


def _resolved_source_path(raw_path: str) -> str:
    return str(Path(raw_path).expanduser().resolve(strict=False))


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


def resource_record(
    *,
    resource_id: object,
    resource_type: object,
    title: object,
    summary: object = "",
    adapter: str,
    path: object = "",
    modified_at: object = "",
    facets: Iterable[object] = (),
    attributes: dict[str, Any] | None = None,
    search_terms: Iterable[object] = (),
    url: object = "",
) -> dict[str, Any]:
    clean_id = stripped_text(resource_id)
    clean_type = stripped_text(resource_type)
    clean_title = stripped_text(title) or clean_id
    clean_summary = stripped_text(summary)
    clean_path = stripped_text(path)
    clean_facets = _dedupe([clean_type, *facets])
    clean_terms = _dedupe([
        clean_id,
        clean_title,
        *(term for term in search_terms if stripped_text(term) != clean_summary),
    ])
    raw_attributes = dict(attributes) if isinstance(attributes, dict) else attributes
    try:
        clean_attributes = normalize_resource_attributes(raw_attributes)
    except (TypeError, ValueError):
        clean_attributes = raw_attributes
    return {
        "resource_id": clean_id,
        "resource_type": clean_type,
        "title": clean_title,
        "summary": clean_summary,
        "source": {
            "adapter": stripped_text(adapter),
            "path": clean_path,
            "modified_at": stripped_text(modified_at) or _path_modified_at(clean_path),
        },
        "facets": clean_facets,
        "attributes": clean_attributes,
        "search_terms": clean_terms,
        "url": portal_url_for_resource(clean_id, clean_type, record={"attributes": clean_attributes}) or stripped_text(url) or f"/r/{quote(clean_id, safe='')}",
    }


def _validate_record(record: dict[str, Any], adapter: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("resource record must be an object")
    resource_id = stripped_text(record.get("resource_id"))
    resource_type = stripped_text(record.get("resource_type"))
    _validate_resource_id(resource_id, resource_type)
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    if stripped_text(source.get("adapter")) != adapter:
        raise ValueError(f"resource {resource_id} is assigned to the wrong adapter")
    if not stripped_text(record.get("title")):
        raise ValueError(f"resource {resource_id} has no title")
    normalized = _json_value(record)
    normalized["facets"] = _dedupe(normalized.get("facets") or [])
    normalized["search_terms"] = _dedupe(normalized.get("search_terms") or [])
    raw_attributes = normalized.get("attributes")
    if not isinstance(raw_attributes, dict):
        raise ValueError(f"resource {resource_id} attributes must be an object")
    normalized["attributes"] = normalize_resource_attributes(raw_attributes)
    return normalized


@dataclass(frozen=True)
class ChangeSet:
    adapter: str
    upserts: tuple[dict[str, Any], ...] = ()
    deletes: tuple[str, ...] = ()
    source_state: dict[str, Any] = field(default_factory=dict)


class IndexStore:
    """Thread-safe projection store with atomic per-adapter publication."""

    def __init__(
        self,
        *,
        portal_access: PortalAccessStore | None = None,
    ) -> None:
        self.database = portal_database(schema=resource_database_schema())
        from management_portal.resources.resource_types import ResourceTypes
        self.resource_types = ResourceTypes()
        self._lock = ReportingLock("resource index")
        self._persist_lock = threading.Lock()
        self._portal_access = portal_access
        self._records: dict[str, dict[str, Any]] = {}
        self._type_ids: dict[str, dict[str, None]] = {}
        self._adapter_ids: dict[str, set[str]] = {}
        self._adapter_status: dict[str, dict[str, Any]] = {}
        self._index_updated_at = ""
        self._change_generation = 0
        # A committed one-resource projection must survive scans which began
        # before that commit, even when their snapshot is published later.
        self._direct_revisions: dict[str, dict[str, int]] = {}
        self._persisted_generation = 0
        self._dirty_upserts: set[str] = set()
        self._dirty_deletes: set[str] = set()
        self._dirty_adapters: set[str] = set()
        self._deleted_adapters: set[str] = set()
        self._initialize_database()
        self._load_database()

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self.database.connect() as connection:
            yield connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_index_records (
                    resource_id TEXT PRIMARY KEY,
                    adapter TEXT NOT NULL,
                    record_json JSONB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS resource_index_records_adapter_idx
                    ON resource_index_records(adapter, resource_id);
                CREATE TABLE IF NOT EXISTS resource_index_adapters (
                    adapter TEXT PRIMARY KEY,
                    status_json JSONB NOT NULL
                );
                """
            )
            row = connection.execute(
                "SELECT value FROM resource_index_meta WHERE key = 'schema'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO resource_index_meta (key, value) VALUES ('schema', %s) "
                    "ON CONFLICT(key) DO NOTHING",
                    (RESOURCE_INDEX_STORE_SCHEMA,),
                )
                # Validate the actual winner if another initializer inserted it.
                row = connection.execute(
                    "SELECT value FROM resource_index_meta WHERE key = 'schema'"
                ).fetchone()
            if row is None or not schema_matches(row["value"], RESOURCE_INDEX_STORE_SCHEMA):
                raise RuntimeError("unsupported ResourceService index schema")

    def _load_database(self) -> None:
        records: dict[str, dict[str, Any]] = {}
        adapter_ids: dict[str, set[str]] = {}
        status: dict[str, dict[str, Any]] = {}
        index_updated_at = ""
        with self._connect() as connection:
            record_rows = connection.execute(
                "SELECT resource_id, adapter, record_json FROM resource_index_records"
            ).fetchall()
            adapter_rows = connection.execute(
                "SELECT adapter, status_json FROM resource_index_adapters"
            ).fetchall()
            updated_row = connection.execute(
                "SELECT value FROM resource_index_meta WHERE key = 'index_updated_at'"
            ).fetchone()
        try:
            for row in record_rows:
                raw = json_value(row["record_json"])
                if not isinstance(raw, dict):
                    raise ValueError("invalid persisted resource record")
                adapter = stripped_text(row["adapter"])
                record = _validate_record(raw, adapter)
                resource_id = record["resource_id"]
                if resource_id in records:
                    raise ValueError(f"duplicate persisted resource id: {resource_id}")
                records[resource_id] = record
                adapter_ids.setdefault(adapter, set()).add(resource_id)
            for row in adapter_rows:
                value = json_value(row["status_json"])
                if not isinstance(value, dict):
                    raise ValueError("invalid resource adapter status")
                status[str(row["adapter"])] = value
            index_updated_at = str(updated_row["value"] or "") if updated_row else ""
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("ResourceService index database is corrupt") from exc
        with self._lock:
            self._records = records
            self._type_ids = {}
            for resource_id, record in records.items():
                self._type_ids.setdefault(record["resource_type"], {})[resource_id] = None
            self._adapter_ids = adapter_ids
            self._adapter_status = _json_value(status)
            self._index_updated_at = index_updated_at

    def _persist(self) -> None:
        """Write pending index changes; readers keep the in-memory index meanwhile.

        The pending changes are taken under the index lock, which is released
        before the database write: queries, gets and change routing would
        otherwise wait for the write and for the schema-wide advisory lock it
        shares with the other Portal instance.  A change made during the write
        marks itself pending again with a newer generation and is written by
        the next call; a failed write puts back what it did not persist.
        """

        with self._persist_lock:
            with self._lock:
                generation = self._change_generation
                if generation <= self._persisted_generation:
                    return
                upserts = set(self._dirty_upserts)
                deletes = set(self._dirty_deletes)
                adapters = set(self._dirty_adapters)
                deleted_adapter_names = set(self._deleted_adapters)
                self._dirty_upserts.clear()
                self._dirty_deletes.clear()
                self._dirty_adapters.clear()
                self._deleted_adapters.clear()
                records = [
                    (
                        resource_id,
                        stripped_text((record.get("source") or {}).get("adapter")),
                        json_text(record, ensure_ascii=False, separators=(",", ":")),
                    )
                    for resource_id in sorted(upserts)
                    if (record := self._records.get(resource_id)) is not None
                ]
                deleted_ids = sorted(deletes)
                statuses = [
                    (
                        adapter,
                        json_text(status, ensure_ascii=False, separators=(",", ":")),
                    )
                    for adapter in sorted(adapters)
                    if (status := self._adapter_status.get(adapter)) is not None
                ]
                deleted_adapters = sorted(deleted_adapter_names)
                index_updated_at = self._index_updated_at
            try:
                with self._connect() as connection:
                    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))")
                    connection.executemany(
                        'DELETE FROM resource_index_records WHERE resource_id = %s',
                        ((resource_id,) for resource_id in deleted_ids),
                    )
                    connection.executemany(
                        '\n                        INSERT INTO resource_index_records\n                            (resource_id, adapter, record_json)\n                        VALUES (%s, %s, %s)\n                        ON CONFLICT(resource_id) DO UPDATE SET\n                            adapter = excluded.adapter,\n                            record_json = excluded.record_json\n                        ',
                        records,
                    )
                    connection.executemany(
                        'DELETE FROM resource_index_adapters WHERE adapter = %s',
                        ((adapter,) for adapter in deleted_adapters),
                    )
                    connection.executemany(
                        '\n                        INSERT INTO resource_index_adapters (adapter, status_json)\n                        VALUES (%s, %s)\n                        ON CONFLICT(adapter) DO UPDATE SET\n                            status_json = excluded.status_json\n                        ',
                        statuses,
                    )
                    connection.execute(
                        "\n                        INSERT INTO resource_index_meta (key, value)\n                        VALUES ('index_updated_at', %s)\n                        ON CONFLICT(key) DO UPDATE SET value = excluded.value\n                        ",
                        (index_updated_at,),
                    )
                    connection.commit()
            except BaseException:
                with self._lock:
                    # A change made during the write is newer than what failed.
                    self._dirty_upserts.update(upserts - self._dirty_deletes)
                    self._dirty_deletes.update(deletes - self._dirty_upserts)
                    self._dirty_adapters.update(adapters - self._deleted_adapters)
                    self._deleted_adapters.update(deleted_adapter_names - self._dirty_adapters)
                raise
            with self._lock:
                self._persisted_generation = max(
                    self._persisted_generation,
                    generation,
                )

    def _mark_records_dirty(
        self,
        *,
        upserts: Iterable[str] = (),
        deletes: Iterable[str] = (),
    ) -> None:
        for resource_id in upserts:
            self._dirty_deletes.discard(resource_id)
            self._dirty_upserts.add(resource_id)
        for resource_id in deletes:
            self._dirty_upserts.discard(resource_id)
            self._dirty_deletes.add(resource_id)

    def _mark_adapters_dirty(
        self,
        *,
        upserts: Iterable[str] = (),
        deletes: Iterable[str] = (),
    ) -> None:
        for adapter in upserts:
            self._deleted_adapters.discard(adapter)
            self._dirty_adapters.add(adapter)
        for adapter in deletes:
            self._dirty_adapters.discard(adapter)
            self._deleted_adapters.add(adapter)

    def flush(self) -> None:
        self._persist()

    def adapter_source_state(self, adapter: str) -> dict[str, Any]:
        with self._lock:
            status = self._adapter_status.get(adapter) or {}
            state = status.get("source_state")
            return _json_value(state) if isinstance(state, dict) else {}

    def adapter_has_snapshot(self, adapter: str) -> bool:
        with self._lock:
            status = self._adapter_status.get(adapter) or {}
            return bool(stripped_text(status.get("last_success_at")))

    def records_for_adapter(self, adapter: str) -> list[dict[str, Any]]:
        # Stored records are replaced, never changed in place, so the copies
        # are made after the lock is released: copying a large adapter's
        # records takes a quarter of a second, and every get and change
        # routing would wait for it.
        with self._lock:
            records = [
                record
                for resource_id in self._adapter_ids.get(adapter) or ()
                if (record := self._records.get(resource_id)) is not None
            ]
        records.sort(key=lambda record: record["resource_id"])
        return [_json_value(record) for record in records]

    def discard_inactive_adapters(
        self,
        active_adapters: Iterable[str],
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        active = {stripped_text(value) for value in active_adapters if stripped_text(value)}
        with self._lock:
            stale = (set(self._adapter_ids) | set(self._adapter_status)) - active
            if not stale:
                return {"adapters": [], "deleted": 0}
            deleted_ids = {
                resource_id
                for resource_id, record in self._records.items()
                if stripped_text((record.get("source") or {}).get("adapter")) in stale
            }
            for resource_id in deleted_ids:
                previous = self._records.pop(resource_id, None)
                if previous:
                    self._type_ids[previous["resource_type"]].pop(resource_id, None)
            for adapter in stale:
                self._adapter_ids.pop(adapter, None)
                self._adapter_status.pop(adapter, None)
            self._index_updated_at = _now_iso()
            self._mark_records_dirty(deletes=deleted_ids)
            self._mark_adapters_dirty(deletes=stale)
            self._change_generation += 1
        if persist:
            self._persist()
        return {"adapters": sorted(stale), "deleted": len(deleted_ids)}

    def apply(
        self, changes: ChangeSet, *, persist: bool = True,
        scan_started_generation: int | None = None,
        direct: bool = False,
    ) -> dict[str, Any]:
        adapter = stripped_text(changes.adapter)
        if not adapter:
            raise ValueError("adapter name required")
        validated: dict[str, dict[str, Any]] = {}
        enabled_types = self.resource_types.enabled_types()
        protected_ids: set[str] = set()
        resource_errors = [
            dict(item)
            for item in changes.source_state.get("resource_errors") or []
            if isinstance(item, dict)
        ]
        for raw in changes.upserts:
            raw_id = stripped_text(raw.get("resource_id")) if isinstance(raw, dict) else ""
            try:
                record = _validate_record(raw, adapter)
                if record['resource_type'] not in enabled_types:
                    continue
                resource_id = record["resource_id"]
                if resource_id in validated:
                    validated.pop(resource_id, None)
                    protected_ids.add(resource_id)
                    raise ValueError(
                        f"adapter {adapter} produced duplicate id {resource_id}"
                    )
                validated[resource_id] = record
            except Exception as exc:
                if raw_id:
                    protected_ids.add(raw_id)
                resource_errors.append(
                    {
                        "resource_id": raw_id,
                        "error": str(exc)[:1000],
                    }
                )
        delete_ids = {stripped_text(value) for value in changes.deletes if stripped_text(value)}
        conflicts = delete_ids & (set(validated) | protected_ids)
        for resource_id in sorted(conflicts):
            delete_ids.discard(resource_id)
            resource_errors.append(
                {
                    "resource_id": resource_id,
                    "error": "ChangeSet cannot upsert and delete the same resource",
                }
            )

        now = _now_iso()
        with self._lock:
            if scan_started_generation is not None:
                newer = {
                    resource_id for resource_id, revision in
                    self._direct_revisions.get(adapter, {}).items()
                    if revision > scan_started_generation
                }
                for resource_id in newer:
                    validated.pop(resource_id, None)
                delete_ids.difference_update(newer)
            current_ids = set(self._adapter_ids.get(adapter) or set())
            for resource_id in tuple(validated):
                existing = self._records.get(resource_id)
                owner = stripped_text((existing or {}).get("source", {}).get("adapter"))
                if existing and owner != adapter:
                    validated.pop(resource_id)
                    resource_errors.append(
                        {
                            "resource_id": resource_id,
                            "error": (
                                f"resource id is already owned by adapter {owner}"
                            ),
                        }
                    )
            for resource_id in tuple(delete_ids):
                existing = self._records.get(resource_id)
                owner = stripped_text((existing or {}).get("source", {}).get("adapter"))
                if existing and owner != adapter:
                    delete_ids.discard(resource_id)
                    resource_errors.append(
                        {
                            "resource_id": resource_id,
                            "error": (
                                f"adapter {adapter} cannot delete resource owned by {owner}"
                            ),
                        }
                    )
            effective_deletes = delete_ids & current_ids
            added_ids = set(validated) - current_ids
            updated_ids = set(validated) & current_ids
            owned_after = (current_ids - effective_deletes) | set(validated)
            previous = self._adapter_status.get(adapter) or {}
            source_state = {
                **changes.source_state,
                "resource_error_count": len(resource_errors),
            }
            if resource_errors:
                source_state["resource_errors"] = resource_errors[:100]
            else:
                source_state.pop("resource_errors", None)
            if validated or effective_deletes:
                for resource_id in effective_deletes | set(validated):
                    previous_record = self._records.get(resource_id)
                    if previous_record and (resource_id in effective_deletes or
                                            validated[resource_id]["resource_type"] != previous_record["resource_type"]):
                        self._type_ids[previous_record["resource_type"]].pop(resource_id, None)
                for resource_id, record in validated.items():
                    self._type_ids.setdefault(record["resource_type"], {})[resource_id] = None
                for resource_id in effective_deletes:
                    self._records.pop(resource_id, None)
                self._records.update(validated)
                self._index_updated_at = now
            self._adapter_ids[adapter] = owned_after
            self._adapter_status[adapter] = {
                **previous,
                "last_checked_at": now,
                "last_success_at": now,
                "last_error": "",
                "resource_error_count": len(resource_errors),
                "resource_count": len(owned_after),
                "source_state": _json_value(source_state),
                "last_changes": {
                    "added": sorted(added_ids)[:100],
                    "deleted": sorted(effective_deletes)[:100],
                    "added_count": len(added_ids),
                    "updated_count": len(updated_ids),
                    "deleted_count": len(effective_deletes),
                },
            }
            self._mark_records_dirty(
                upserts=validated,
                deletes=effective_deletes,
            )
            self._mark_adapters_dirty(upserts=(adapter,))
            self._change_generation += 1
            if direct:
                revisions = self._direct_revisions.setdefault(adapter, {})
                for resource_id in set(validated) | delete_ids:
                    revisions[resource_id] = self._change_generation
            index_updated_at = self._index_updated_at
        if persist:
            self._persist()
        return {
            "adapter": adapter,
            "upserted": len(validated),
            "deleted": len(effective_deletes),
            "upserted_ids": sorted(validated),
            "deleted_ids": sorted(effective_deletes),
            "resource_error_count": len(resource_errors),
            "resource_count": len(owned_after),
            "index_updated_at": index_updated_at,
        }

    def publish_snapshot(
        self,
        adapter: str,
        records: Iterable[dict[str, Any]],
        source_state: dict[str, Any],
        *,
        persist: bool = True,
        scan_started_generation: int | None = None,
    ) -> dict[str, Any]:
        # Stored records are replaced, never changed in place: resolving the
        # adapter's source paths works on a snapshot outside the lock.
        with self._lock:
            previous_ids = set(self._adapter_ids.get(adapter) or set())
            current_records = list(self._records.items())
        records_by_other_adapters = {
            resource_id: record
            for resource_id, record in current_records
            if stripped_text((record.get("source") or {}).get("adapter")) != adapter
        }
        # Only an error without an ID needs the records by source path, and
        # resolving every source path costs one system call per component.
        previous_by_path: dict[str, set[str]] | None = None

        def previous_ids_for_path(error_path: str) -> set[str]:
            nonlocal previous_by_path
            if previous_by_path is None:
                previous_by_path = {}
                for resource_id, record in current_records:
                    raw_path = stripped_text((record.get("source") or {}).get("path"))
                    if resource_id in previous_ids and raw_path:
                        previous_by_path.setdefault(
                            _resolved_source_path(raw_path), set()
                        ).add(resource_id)
            return previous_by_path.get(_resolved_source_path(error_path), set())

        incoming_by_id: dict[str, dict[str, Any]] = {}
        rejected_ids: set[str] = set()
        protected_ids: set[str] = set()
        resource_errors = [
            dict(item)
            for item in source_state.get("resource_errors") or []
            if isinstance(item, dict)
        ]
        for error in resource_errors:
            resource_id = stripped_text(error.get("resource_id"))
            error_path = stripped_text(error.get("path"))
            if not resource_id and error_path:
                matched_ids = previous_ids_for_path(error_path)
                protected_ids.update(matched_ids)
                if len(matched_ids) == 1:
                    error["resource_id"] = next(iter(matched_ids))
            if resource_id in previous_ids:
                protected_ids.add(resource_id)
        for raw in records:
            raw_id = stripped_text(raw.get("resource_id")) if isinstance(raw, dict) else ""
            try:
                record = _validate_record(raw, adapter)
                resource_id = record["resource_id"]
                if resource_id in incoming_by_id or resource_id in rejected_ids:
                    incoming_by_id.pop(resource_id, None)
                    rejected_ids.add(resource_id)
                    if resource_id in previous_ids:
                        protected_ids.add(resource_id)
                    raise ValueError(
                        f"adapter {adapter} produced duplicate id {resource_id}"
                    )
                owner = stripped_text(
                    (records_by_other_adapters.get(resource_id) or {})
                    .get("source", {})
                    .get("adapter")
                )
                if owner:
                    raise ValueError(
                        f"resource id {resource_id} is already owned by adapter {owner}"
                    )
                incoming_by_id[resource_id] = record
            except Exception as exc:
                if raw_id in previous_ids:
                    protected_ids.add(raw_id)
                resource_errors.append(
                    {
                        "resource_id": raw_id,
                        "error": str(exc)[:1000],
                    }
                )
        incoming_ids = set(incoming_by_id) | protected_ids
        next_source_state = {
            **source_state,
            "resource_error_count": len(resource_errors),
        }
        if resource_errors:
            next_source_state["resource_errors"] = resource_errors[:100]
        else:
            next_source_state.pop("resource_errors", None)
        if protected_ids:
            next_source_state["preserved_resource_ids"] = sorted(protected_ids)[:100]
        else:
            next_source_state.pop("preserved_resource_ids", None)
        with self._lock:
            deletes = tuple(sorted(previous_ids - incoming_ids))
            changed = tuple(
                record
                for resource_id, record in incoming_by_id.items()
                if self._records.get(resource_id) != record
            )
            initial_publish = adapter not in self._adapter_status
        return self.apply(
            ChangeSet(
                adapter=adapter,
                upserts=changed,
                deletes=deletes,
                source_state={
                    **next_source_state,
                    "initial_publish": initial_publish,
                },
            ),
            persist=persist,
            scan_started_generation=scan_started_generation,
        )

    def generation(self) -> int:
        with self._lock:
            return self._change_generation

    def mark_checked(
        self,
        adapter: str,
        source_state: dict[str, Any],
        *,
        persist: bool = True,
    ) -> None:
        now = _now_iso()
        with self._lock:
            previous = self._adapter_status.get(adapter) or {}
            self._adapter_status[adapter] = {
                **previous,
                "last_checked_at": now,
                "last_error": "",
                "resource_count": len(self._adapter_ids.get(adapter) or set()),
                "source_state": _json_value(source_state),
            }
            self._mark_adapters_dirty(upserts=(adapter,))
            self._change_generation += 1
        if persist:
            self._persist()

    def mark_error(
        self,
        adapter: str,
        error: BaseException,
        *,
        persist: bool = True,
    ) -> None:
        now = _now_iso()
        with self._lock:
            previous = self._adapter_status.get(adapter) or {}
            self._adapter_status[adapter] = {
                **previous,
                "last_checked_at": now,
                "last_error": str(error)[:1000],
                "resource_count": len(self._adapter_ids.get(adapter) or set()),
            }
            self._mark_adapters_dirty(upserts=(adapter,))
            self._change_generation += 1
        if persist:
            self._persist()


    def type_counts(self) -> dict[str, int]:
        with self._lock:
            return {kind: len(ids) for kind, ids in self._type_ids.items()}

    def query(
        self,
        *,
        actor: str = "",
        q: str = "",
        resource_types: Iterable[str] = (),
        facets: Iterable[str] = (),
        projects: Iterable[str] = (),
        statuses: Iterable[str] = (),
        attribute_filters: dict[str, Iterable[str]] | None = None,
        limit: int = 50,
        offset: int = 0,
        include_attributes: bool = False,
        records_projector: ResourceRecordsProjector | None = None,
        records_projector_types: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        enabled_types = self.resource_types.enabled_types()
        query = ResourceQuery.from_kwargs(
            {
                "actor": actor,
                "q": q,
                "resource_types": resource_types,
                "facets": facets,
                "projects": projects,
                "statuses": statuses,
                "attribute_filters": attribute_filters,
                "limit": limit,
                "offset": offset,
                "include_attributes": include_attributes,
            }
        )
        with timing.timed_request() as timer:
            records, updated_at, mark = self._matching_records(
                query, timer, enabled_types=enabled_types,
                records_projector=records_projector, records_projector_types=records_projector_types,
            )
            result = query_records(
                records,
                query,
                index_updated_at=updated_at,
                portal_access=self._portal_access,
            )
            mark = timer.add("rank_and_copy", mark)
            self._with_current_capabilities(result["resources"], actor)
            self._set_current_urls(result["resources"], records)
            timer.add("canonical_urls", mark)
            timer.report_if_slow(
                "query", SLOW_QUERY_SECONDS,
                candidates=len(records), returned=len(result.get("resources") or []),
                facets=sorted(query.facets), resource_types=sorted(query.resource_types),
            )
        return result

    def project_changes(
        self,
        projects: Iterable[str],
        *,
        actor: str = "",
        records_projector: ResourceRecordsProjector | None = None,
        records_projector_types: frozenset[str] | None = None,
    ) -> dict[str, str]:
        """Each Project's latest source change among the resources the actor may read.

        The resources are those a ``projects`` query returns, found in one
        pass without ranking, copying or URLs. Paging such a query 5,000 rows
        at a time projected every candidate again for each page.
        """
        query = ResourceQuery.from_kwargs({"actor": actor, "projects": projects})
        changed: dict[str, str] = {}
        if not query.projects:
            return changed
        with timing.timed_request() as timer:
            records, _updated_at, mark = self._matching_records(
                query, timer, enabled_types=self.resource_types.enabled_types(),
                records_projector=records_projector, records_projector_types=records_projector_types,
            )
            for record, readable in zip(records, readable_flags(records, query.actor)):
                if not readable:
                    continue
                moment = str((record.get("source") or {}).get("modified_at") or "")
                for project_id in (record.get("attributes") or {}).get("project_ids") or ():
                    project_id = str(project_id)
                    if project_id in query.projects and moment > changed.get(project_id, ""):
                        changed[project_id] = moment
            timer.add("readable_changes", mark)
            timer.report_if_slow("project_changes", SLOW_QUERY_SECONDS,
                                 candidates=len(records), projects=len(query.projects))
        return changed

    def _matching_records(
        self,
        query: ResourceQuery,
        timer: Any,
        *,
        enabled_types: Iterable[str],
        records_projector: ResourceRecordsProjector | None,
        records_projector_types: frozenset[str] | None,
    ) -> tuple[list[dict[str, Any]], str, bool, float]:
        """Records passing every filter except read access, with current descriptors and facts.

        Returns them with the index revision and the timer's last mark.
        """
        mark = timer.started
        with self._lock:
            if query.resource_types:
                records = [self._records[resource_id]
                           for resource_type in query.resource_types
                           for resource_id in self._type_ids.get(resource_type, ())]
            else:
                records = list(self._records.values())
            updated_at = self._index_updated_at
        mark = timer.add("snapshot", mark)
        records = [record for record in records if record['resource_type'] in enabled_types]
        records = prefilter_query_candidates(records, query)
        mark = timer.add("prefilter", mark)
        if records_projector is not None and (
            records_projector_types is None or
            any(record["resource_type"] in records_projector_types for record in records)
        ):
            records = records_projector(records)
        mark = timer.add("descriptors", mark)
        current_facts_needed = any(
            record["resource_type"] in {"resident", "lineage", "workspace", "workbench", "family"}
            for record in records
        )
        if current_facts_needed:
            records = self._project_business_facts(records)
        mark = timer.add("business_facts", mark)
        records = filter_query_candidates(records, query)
        mark = timer.add("filter", mark)
        records = self._filter_private_records(records, actor=query.actor)
        mark = timer.add("private_visibility", mark)
        return records, updated_at, mark

    def suggest(
        self,
        *,
        actor: str = "",
        q: str = "",
        limit: int = 30,
        resource_type: str = "",
        records_projector: ResourceRecordsProjector | None = None,
    ) -> dict[str, Any]:
        enabled_types = self.resource_types.enabled_types()
        query = ResourceQuery.from_kwargs({
            "actor": actor, "q": q, "limit": limit,
            "resource_types": [resource_type] if resource_type else [],
        })
        if not query.q:
            return {"resources": []}
        with self._lock:
            if query.resource_types:
                records = [self._records[resource_id]
                           for resource_type in query.resource_types
                           for resource_id in self._type_ids.get(resource_type, ())]
            else:
                records = list(self._records.values())
        ranked = [
            (score, record)
            for record in records
            if record.get('resource_type') in enabled_types
            if not query.resource_types or record.get("resource_type") in query.resource_types
            if (score := score_id_title(record, query.q)) > 0
        ]
        ranked.sort(key=lambda row: (
            -row[0],
            stripped_text(row[1].get("resource_type")),
            stripped_text(row[1].get("title")).casefold(),
            stripped_text(row[1].get("resource_id")),
        ))
        visible: list[dict[str, Any]] = []
        batch_size = query.limit
        access_query = ResourceQuery(actor=query.actor, limit=batch_size)
        for start in range(0, len(ranked), batch_size):
            batch = ranked[start:start + batch_size]
            records = [record for _score_value, record in batch]
            if records_projector is not None:
                records = records_projector(records)
            records = self._project_business_facts(records)
            records = filter_query_candidates(records, access_query)
            if not records:
                continue
            records = self._filter_private_records(records, actor)
            projected = query_records(
                records,
                access_query,
                portal_access=self._portal_access,
            )
            self._set_current_urls(projected["resources"], records)
            by_id = {item["resource_id"]: item
                     for item in self._with_current_capabilities(projected["resources"], actor)}
            for score, record in batch:
                item = by_id.get(record["resource_id"])
                if item is None:
                    continue
                item["score"] = round(score, 3)
                visible.append(item)
                if len(visible) == query.limit:
                    return {"resources": visible}
        return {"resources": visible}

    def get(self, resource_id: str, *, actor: str = "") -> dict[str, Any]:
        clean_id = stripped_text(resource_id)
        with self._lock:
            record = self._records.get(clean_id)
            record = _json_value(record) if record else None
        item = self._record_with_current_facts(record, actor) if record else None
        visible = self._apply_record_access([item], actor) if item else []
        if not visible or not visible[0]["capabilities"]["read"]:
            raise KeyError(clean_id)
        item.pop("search_terms", None)
        return item

    def authorization_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"revision": self._index_updated_at, "records": [
                {"resource_id": record["resource_id"], "resource_type": record["resource_type"],
                 "title": record.get("title", ""), "attributes": _json_value(record.get("attributes") or {})}
                for record in self._records.values()
            ]}

    @staticmethod
    def _set_current_urls(items: list[dict[str, Any]], records: Iterable[dict[str, Any]]) -> None:
        """Returned rows carry the Portal surface current code gives them; a cached url may be stale."""
        wanted = {item["resource_id"] for item in items}
        sources = {record["resource_id"]: record for record in records if record["resource_id"] in wanted}
        for item in items:
            current_url = portal_url_for_resource(
                item["resource_id"], item["resource_type"], record=sources[item["resource_id"]],
            )
            if current_url:
                item["url"] = current_url

    @staticmethod
    def _record_for_read(record: dict[str, Any]) -> dict[str, Any]:
        item = _json_value(record)
        current_url = portal_url_for_resource(
            item.get("resource_id"), item.get("resource_type"), record=item,
        )
        if current_url:
            item["url"] = current_url
        return item

    def _project_business_facts(
        self,
        records: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Every query uses fresh Family facts, including with a custom projector.

        Only Family rows change; every other candidate passes through as the
        indexed record, which nothing downstream modifies (query_records copies
        what it returns), and gets its current URL once it is returned.  A copy
        and a URL for each candidate here cost up to 0.6 s per query over the
        ~14,000 candidates a Project-scoped query projects.
        """
        from management_portal.society_service.business_fact_views import project_resources as domain_facts
        return domain_facts(list(records))

    def _apply_record_access(
        self,
        rows: list[dict[str, Any]],
        actor: str,
    ) -> list[dict[str, Any]]:
        return self._with_current_capabilities(self._filter_private_records(rows, actor), actor)

    def _filter_private_records(
        self,
        rows: list[dict[str, Any]],
        actor: str,
    ) -> list[dict[str, Any]]:
        from management_portal.society_service.lineage_access_policy import hidden_private_ids

        hidden = hidden_private_ids(actor, rows)
        return [item for item in rows if item.get("resource_id") not in hidden]

    def _with_current_capabilities(self, items: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
        """A request-time view; business facts and stored descriptors stay unchanged.

        The whole page is decided in one permission batch. A decision per item
        reads the scheme's snapshot once per resource: ListProjects reads every
        resource of every Project and took 10 to 60 seconds that way.
        """
        for item, allowed in zip(items, readable_flags(items, actor)):
            item["capabilities"] = {name: allowed for name in ("discover", "read", "manage")}
        return items

    def _record_with_current_facts(
        self,
        record: dict[str, Any],
        actor: str,
    ) -> dict[str, Any] | None:
        item = self._record_for_read(record)
        if item.get("resource_type") == "family":
            from management_portal.society_service.business_fact_views import project_resources
            return project_resources([item])[0]
        return item

    def internal_get(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(stripped_text(resource_id))
            if not record:
                raise KeyError(resource_id)
            return _json_value(record)

    def status(self, running: Iterable[str] = (), pending: Iterable[str] = ()) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "index_updated_at": self._index_updated_at,
                "resource_count": len(self._records),
                "running_adapters": sorted(set(running)),
                "pending_adapters": sorted(set(pending)),
                "adapters": _json_value(self._adapter_status),
            }


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


@dataclass
class _RefreshCompletion:
    done: bool = False
    ok: bool = False


@dataclass
class _PendingRefresh:
    force: bool = False
    # A full refresh someone forced (overflow, the refresh API), as opposed to
    # a changed path that joined a pending check.
    rescan: bool = False
    full: bool = False
    paths: set[str] = field(default_factory=set)
    queued_at: float = 0.0
    completion: _RefreshCompletion = field(default_factory=_RefreshCompletion)


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


def _current_projection_code_fingerprint() -> str:
    digest = hashlib.sha256()
    # Parsers determine persisted titles and summaries just as this adapter
    # does. Their changes must invalidate unchanged files' old projections.
    for source in (__file__, knowledge_backend.__file__, skill_backend.__file__):
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


class AdapterLayer:
    """Apply explicit source changes to the persisted resource projection."""

    def __init__(
        self,
        store: IndexStore,
        adapters: Iterable[ResourceAdapter],
        *,
        start_worker: bool = True,
        on_projection_changed: Callable[[str, dict[str, Any]], None] | None = None,
        reconcile_seconds: float = RESOURCE_RECONCILE_SECONDS,
        merge_seconds: float = 0.1,
    ) -> None:
        self.store = store
        self.adapters = {adapter.name: adapter for adapter in adapters}
        self._on_projection_changed = on_projection_changed
        self._reconcile_seconds = max(0.05, float(reconcile_seconds))
        self._merge_seconds = max(0.0, float(merge_seconds))
        self._queue: queue.Queue[str] = queue.Queue()
        self._condition = threading.Condition(threading.RLock())
        self._pending: dict[str, _PendingRefresh] = {}
        self._latest_refresh: dict[str, _RefreshCompletion] = {}
        self._running: set[str] = set()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._file_detector: RecursiveFileChangeWatcher | None = None
        # Real paths of each adapter's sources from its last change check;
        # a changed-file update reuses them instead of resolving every record.
        self._source_resolutions: dict[str, dict[str, Path]] = {}
        if start_worker:
            if WATCH_FILESYSTEM:
                directories = _watch_directories({
                    path
                    for adapter in self.adapters.values()
                    for path in (
                        adapter.watch_directories()
                        if callable(getattr(adapter, "watch_directories", None))
                        else []
                    )
                })
                self._file_detector = RecursiveFileChangeWatcher(
                    directories,
                    self._handle_file_change,
                    self._handle_file_overflow,
                    thread_name="portal-resource-index-files",
                    recursive=False,
                    track_new_directories=True,
                )
                self._file_detector.start()
            self._worker = threading.Thread(
                target=self._run,
                name="portal-resource-index",
                daemon=True,
            )
            self._worker.start()

    def _handle_file_change(self, path: str) -> None:
        if not self._stop_event.is_set():
            self.notify_change(path=path)

    def _handle_file_overflow(self) -> None:
        if not self._stop_event.is_set():
            self.request_refresh(force=True)

    def _completed_refresh(
        self,
        adapter: str,
        result: dict[str, Any],
        *,
        forced: bool,
        emit: bool,
    ) -> dict[str, Any]:
        if self._stop_event.is_set():
            return result
        if result.get("scanned") and not result.get("incremental"):
            self._sync_file_watches()
        callback = self._on_projection_changed
        if emit and callback is not None and (forced or bool(result.get("changed"))):
            try:
                callback(adapter, dict(result))
            except Exception:
                LOGGER.exception(
                    "resource projection committed but notification failed: %s",
                    adapter,
                )
        return result

    def _sync_file_watches(self) -> None:
        detector = self._file_detector
        if detector is None:
            return
        directories = _watch_directories({
            path
            for adapter in self.adapters.values()
            for path in (
                adapter.watch_directories()
                if callable(getattr(adapter, "watch_directories", None))
                else []
            )
        })
        detector.add_directories(directories)

    def stop(self, *, timeout: float = 180.0) -> None:
        with self._condition:
            self._stop_event.set()
            self._pending.clear()
            self._condition.notify_all()
        self._queue.put("")
        if self._file_detector is not None:
            self._file_detector.stop()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                raise RuntimeError("resource index worker did not stop before store flush")
        self.store.flush()

    def request_refresh(
        self,
        adapters: Iterable[str] = (),
        *,
        force: bool = False,
        paths: Iterable[str] = (),
    ) -> list[str]:
        names = list(adapters) or list(self.adapters)
        unknown = sorted(set(names) - set(self.adapters))
        if unknown:
            raise ValueError(f"unknown resource index adapter: {', '.join(unknown)}")
        clean_paths = {stripped_text(path) for path in paths if stripped_text(path)}
        rescan = bool(force) and not clean_paths
        queued: list[str] = []
        with self._condition:
            if self._stop_event.is_set():
                return []
            for name in names:
                previous = self._pending.get(name)
                if previous is None:
                    self._pending[name] = _PendingRefresh(
                        force=bool(force),
                        rescan=rescan,
                        full=not clean_paths,
                        paths=set(clean_paths),
                        queued_at=time.monotonic(),
                    )
                    self._latest_refresh[name] = self._pending[name].completion
                    self._queue.put(name)
                    queued.append(name)
                else:
                    previous.force = previous.force or bool(force)
                    previous.rescan = previous.rescan or rescan
                    previous.full = previous.full or not clean_paths
                    previous.paths.update(clean_paths)
            self._condition.notify_all()
        return queued

    def matching_adapters(self, *, path: str = "", adapters: Iterable[str] = ()) -> list[str]:
        names = [stripped_text(value) for value in adapters if stripped_text(value)]
        registry_change = parse_registry_change_key(path)
        if registry_change is not None:
            names.extend(
                name for name, adapter in self.adapters.items()
                if registry_change[0] in getattr(adapter, "registry_collections", ())
            )
        elif path:
            resolved = Path(path).expanduser().resolve(strict=False)
            token = _routing_paths.set({str(path): resolved, str(resolved): resolved})
            try:
                names.extend(
                    name for name, adapter in self.adapters.items() if adapter.owns_path(resolved)
                )
            finally:
                _routing_paths.reset(token)
        return _dedupe(names)

    def notify_change(self, *, path: str = "", adapters: Iterable[str] = ()) -> list[str]:
        matched = self.matching_adapters(path=path, adapters=adapters)
        return self.request_refresh(
            matched,
            force=True,
            paths=[path] if path else [],
        ) if matched else []

    def project_registered(
        self, resource_type: str, record: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish one committed registration without waiting for the scan queue."""
        resource_id = stripped_text(record.get("resource_id"))
        owners = [adapter for adapter in self.adapters.values()
                  if resource_type in adapter.resource_types]
        if len(owners) != 1:
            raise ValueError(f"registration has no unique index adapter: {resource_type}")
        adapter = owners[0]
        projector = getattr(adapter, "project_record", None)
        if callable(projector):
            summary = projector(resource_type, record)
        else:
            path = stripped_text(record.get("path"))
            scan_changes = getattr(adapter, "scan_changes", None)
            if not path or not callable(scan_changes):
                raise ValueError(f"adapter {adapter.name} cannot project one registration")
            changes = scan_changes(
                (path,), self.store.records_for_adapter(adapter.name),
                self.store.adapter_source_state(adapter.name),
            )
            matches = [item for item in changes.upserts
                       if stripped_text(item.get("resource_id")) == resource_id]
            if len(matches) != 1:
                raise RuntimeError(f"adapter {adapter.name} did not find registered {resource_id}")
            summary = matches[0]
        if stripped_text(summary.get("resource_id")) != resource_id:
            raise ValueError("registration projection changed resource identity")
        result = self.store.apply(
            ChangeSet(adapter.name, upserts=(summary,),
                      source_state=self.store.adapter_source_state(adapter.name)),
            direct=True,
        )
        if resource_id not in result["upserted_ids"]:
            raise RuntimeError(f"registration projection rejected {resource_id}")
        self._completed_refresh(adapter.name, {
            **result, "changed": True, "scanned": False, "incremental": True,
        }, forced=True, emit=True)
        return result

    def project_unregistered(self, resource_type: str, resource_id: str) -> dict[str, Any]:
        """Remove a committed identity before callers query the derived list.

        The direct revision also fences scans begun before the unregistration,
        including a scan whose first observation of this ID is still in flight.
        """
        owners = [adapter for adapter in self.adapters.values()
                  if resource_type in adapter.resource_types]
        if not owners:
            raise ValueError(f"unregistration has no index adapter: {resource_type}")
        try:
            record = self.store.internal_get(resource_id)
        except KeyError:
            record = None
        if record is not None:
            owner = record["source"]["adapter"]
            if record["resource_type"] != resource_type or owner not in {a.name for a in owners}:
                raise ValueError(f"unregistration index ownership mismatch: {resource_id}")
            # A type may have several sources (Knowledge and Paper reviews).
            # Delete through the recorded owner before fencing the other sources;
            # adapters must never delete a record belonging to another adapter.
            owners.sort(key=lambda adapter: adapter.name != owner)
        results = []
        for adapter in owners:
            result = self.store.apply(ChangeSet(
                adapter.name, deletes=(resource_id,),
                source_state=self.store.adapter_source_state(adapter.name),
            ), direct=True)
            self._completed_refresh(adapter.name, {
                **result, "changed": True, "scanned": False, "incremental": True,
            }, forced=True, emit=True)
            results.append(result)
        return {
            **results[0],
            "adapters": [adapter.name for adapter in owners],
            "deleted": sum(result["deleted"] for result in results),
            "deleted_ids": sorted({rid for result in results for rid in result["deleted_ids"]}),
        }

    def refresh_now(
        self,
        adapter_name: str,
        *,
        force: bool = False,
        paths: Iterable[str] = (),
        rescan: bool | None = None,
        joined_paths: Iterable[str] = (),
        _claimed: bool = False,
    ) -> dict[str, Any]:
        """Refresh one adapter.

        ``paths`` asks for an incremental update of those sources.  Without
        them the adapter is checked as a whole.  An adapter that tracks its
        source changes re-reads only the changed sources, unless ``rescan``
        (by default ``force``) asks for a full scan; ``joined_paths`` are
        changed paths that arrived while the check waited.  Other adapters
        scan when ``force`` is set or their fingerprint changed.
        """
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            raise ValueError(f"unknown resource index adapter: {adapter_name}")
        if not _claimed:
            with self._condition:
                if adapter_name in self._running:
                    raise RuntimeError(f"adapter refresh already running: {adapter_name}")
                self._running.add(adapter_name)
                direct_completion = _RefreshCompletion()
                if adapter_name not in self._pending:
                    self._latest_refresh[adapter_name] = direct_completion
        succeeded = True
        started = time.perf_counter()
        scan_started_generation = self.store.generation()
        rescan = force if rescan is None else bool(rescan)
        joined = tuple(sorted({stripped_text(path) for path in joined_paths if stripped_text(path)}))
        try:
            check_scan_cancelled()
            clean_paths = tuple(sorted({stripped_text(path) for path in paths if stripped_text(path)}))
            incremental_scan = getattr(adapter, "scan_changes", None)
            if clean_paths and callable(incremental_scan):
                previous_records = self.store.records_for_adapter(adapter_name)
                previous_state = self.store.adapter_source_state(adapter_name)
                token = _known_paths.set(self._source_resolutions.get(adapter_name))
                try:
                    changes = self._scan_paths(
                        adapter_name,
                        incremental_scan,
                        clean_paths,
                        previous_records,
                        previous_state,
                    )
                finally:
                    _known_paths.reset(token)
                if changes is not None:
                    return self._apply_incremental(
                        adapter_name,
                        changes,
                        clean_paths,
                        previous_records,
                        previous_state,
                        {"incremental_checked_at": _now_iso()},
                        started=started,
                        force=force,
                        claimed=_claimed,
                        scan_started_generation=scan_started_generation,
                    )
                # The adapter cannot apply these paths alone.
                incremental_failed = True
            else:
                incremental_failed = False
            previous_state = self.store.adapter_source_state(adapter_name)
            source_scope = getattr(adapter, "source_scope", None)
            tracks_changes = callable(source_scope) and callable(incremental_scan)
            check_started = time.time()
            if tracks_changes:
                if (
                    not rescan
                    and not incremental_failed
                    and self.store.adapter_has_snapshot(adapter_name)
                ):
                    checked = self._refresh_changed_sources(
                        adapter_name,
                        adapter,
                        previous_state,
                        check_started,
                        joined,
                        started=started,
                        force=force,
                        claimed=_claimed,
                        scan_started_generation=scan_started_generation,
                    )
                    if checked is not None:
                        return checked
                scan_state = self._source_tracking_state(adapter, source_scope(), check_started)
            else:
                fingerprint = adapter.fingerprint()
                check_scan_cancelled()
                if (
                    not force
                    and self.store.adapter_has_snapshot(adapter_name)
                    and stripped_text(previous_state.get("fingerprint")) == fingerprint
                ):
                    self.store.mark_checked(
                        adapter_name,
                        {
                            **previous_state,
                            "fingerprint": fingerprint,
                            "projection_code_fingerprint": _adapter_projection_code_fingerprint(adapter),
                            "checked_at": _now_iso(),
                        },
                        persist=not _claimed,
                    )
                    return self._unchanged_refresh(adapter_name, started, force=force, claimed=_claimed)
                scan_state = {"fingerprint": fingerprint}
            check_scan_cancelled()
            records, source_state = adapter.scan()
            check_scan_cancelled()
            projection_changed = bool(source_state.get("projection_signature")) and (
                stripped_text(source_state.get("projection_signature"))
                != stripped_text(previous_state.get("projection_signature"))
            )
            recover = getattr(adapter, "recover_snapshot", None)
            if callable(recover):
                records, source_state = recover(
                    self.store.records_for_adapter(adapter_name),
                    records,
                    source_state,
                )
            check_scan_cancelled()
            published = self.store.publish_snapshot(
                adapter_name,
                records,
                {
                    **source_state,
                    **scan_state,
                    "projection_code_fingerprint": _adapter_projection_code_fingerprint(adapter),
                    "checked_at": _now_iso(),
                },
                persist=not _claimed,
                scan_started_generation=scan_started_generation,
            )
            return self._completed_refresh(adapter_name, {
                **published,
                "changed": bool(
                    published.get("upserted")
                    or published.get("deleted")
                    or projection_changed
                ),
                "scanned": True,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }, forced=force, emit=not _claimed)
        except Exception as exc:
            succeeded = False
            self.store.mark_error(
                adapter_name,
                exc,
                persist=not _claimed,
            )
            raise
        finally:
            if not _claimed:
                with self._condition:
                    direct_completion.ok = succeeded
                    direct_completion.done = True
                    self._running.discard(adapter_name)
                    self._condition.notify_all()

    def _unchanged_refresh(
        self,
        adapter_name: str,
        started: float,
        *,
        force: bool,
        claimed: bool,
    ) -> dict[str, Any]:
        return self._completed_refresh(adapter_name, {
            "adapter": adapter_name,
            "changed": False,
            "resource_count": int(
                (self.store.status().get("adapters", {}).get(adapter_name) or {}).get("resource_count") or 0
            ),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }, forced=force, emit=not claimed)

    def _scan_paths(
        self,
        adapter_name: str,
        incremental_scan: Callable[..., ChangeSet | None],
        paths: tuple[str, ...],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
    ) -> ChangeSet | None:
        try:
            return incremental_scan(paths, previous_records, previous_state)
        except Exception:
            return self._retry_incremental_paths(
                adapter_name,
                incremental_scan,
                paths,
                previous_records,
                previous_state,
            )

    def _apply_incremental(
        self,
        adapter_name: str,
        changes: ChangeSet,
        paths: tuple[str, ...],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
        state: dict[str, Any],
        *,
        started: float,
        force: bool,
        claimed: bool,
        scan_started_generation: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(changes, ChangeSet) or changes.adapter != adapter_name:
            raise ValueError(f"adapter {adapter_name} returned an invalid incremental ChangeSet")
        previous_by_id = {
            record["resource_id"]: record for record in previous_records
        }
        changed_upserts = tuple(
            record
            for record in changes.upserts
            if previous_by_id.get(stripped_text(record.get("resource_id"))) != record
        )
        changed_deletes = tuple(
            resource_id
            for resource_id in changes.deletes
            if resource_id in previous_by_id
        )
        incremental_errors = self._incremental_resource_errors(
            previous_state,
            changes.source_state,
            paths,
        )
        check_scan_cancelled()
        published = self.store.apply(
            ChangeSet(
                adapter=adapter_name,
                upserts=changed_upserts,
                deletes=changed_deletes,
                source_state={
                    **previous_state,
                    **changes.source_state,
                    "resource_errors": incremental_errors,
                    **state,
                },
            ),
            persist=not claimed,
            scan_started_generation=scan_started_generation,
        )
        return self._completed_refresh(adapter_name, {
            **published,
            "changed": bool(changed_upserts or changed_deletes),
            "scanned": True,
            "incremental": True,
            "path_count": len(paths),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }, forced=force, emit=not claimed)

    @staticmethod
    def _source_tracking_state(
        adapter: ResourceAdapter,
        scope: dict[str, Any],
        check_started: float,
    ) -> dict[str, Any]:
        """What a change check compares against next time, taken before scanning."""
        return {
            "sources_since": check_started,
            "projection_code_fingerprint": _adapter_projection_code_fingerprint(adapter),
            "registry_fingerprint": (
                adapter._registry_fingerprint()
                if getattr(adapter, "registry_collections", ())
                else ""
            ),
            "present_files": sorted(
                path for path in scope.get("files") or ()
                if os.path.exists(path)
            ),
        }

    def _refresh_changed_sources(
        self,
        adapter_name: str,
        adapter: ResourceAdapter,
        previous_state: dict[str, Any],
        check_started: float,
        joined_paths: tuple[str, ...],
        *,
        started: float,
        force: bool,
        claimed: bool,
        scan_started_generation: int,
    ) -> dict[str, Any] | None:
        """Apply only the sources changed since the previous check.

        Returns None when the adapter has to be scanned as a whole: it was
        never checked this way, the index code or the registries it reads
        changed, or the adapter cannot apply the changed paths alone.
        """
        since = previous_state.get("sources_since")
        scope = adapter.source_scope()
        tracking = self._source_tracking_state(adapter, scope, check_started)
        if (
            not isinstance(since, (int, float))
            or stripped_text(previous_state.get("projection_code_fingerprint")) != tracking["projection_code_fingerprint"]
            or stripped_text(previous_state.get("registry_fingerprint")) != tracking["registry_fingerprint"]
        ):
            return None
        previous_records = self.store.records_for_adapter(adapter_name)
        sources = {
            source_path
            for record in previous_records
            if (source_path := stripped_text((record.get("source") or {}).get("path")))
        }
        resolution_paths = set(sources)
        extra_paths = getattr(adapter, "source_resolution_paths", None)
        if callable(extra_paths):
            resolution_paths.update(extra_paths(previous_records))
        found = _find_source_changes({
            **scope,
            "since": float(since) - SOURCE_CHANGE_MARGIN_SECONDS,
            "paths": sorted(resolution_paths),
        })
        known = {raw: Path(real) for raw, real in (found.get("resolved") or {}).items()}
        self._source_resolutions[adapter_name] = known
        vanished = set(previous_state.get("present_files") or ()) - set(tracking["present_files"])
        changed_paths = tuple(sorted(
            set(found.get("changed") or ())
            | (set(found.get("missing") or ()) & sources)
            | vanished
            | set(joined_paths)
        ))
        state = {**tracking, "checked_at": _now_iso()}
        if not changed_paths:
            self.store.mark_checked(
                adapter_name,
                {**previous_state, **state},
                persist=not claimed,
            )
            return self._unchanged_refresh(adapter_name, started, force=force, claimed=claimed)
        token = _known_paths.set(known)
        try:
            changes = self._scan_paths(
                adapter_name,
                adapter.scan_changes,
                changed_paths,
                previous_records,
                previous_state,
            )
            if changes is None:
                return None
            return self._apply_incremental(
                adapter_name,
                changes,
                changed_paths,
                previous_records,
                previous_state,
                state,
                started=started,
                force=force,
                claimed=claimed,
                scan_started_generation=scan_started_generation,
            )
        finally:
            _known_paths.reset(token)

    @staticmethod
    def _incremental_resource_errors(
        previous_state: dict[str, Any],
        current_state: dict[str, Any],
        changed_paths: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        changed = [
            Path(path).expanduser().resolve(strict=False)
            for path in changed_paths
        ]

        def affected(error: dict[str, Any]) -> bool:
            raw_path = stripped_text(error.get("path"))
            if not raw_path:
                return False
            path = Path(raw_path).expanduser().resolve(strict=False)
            return any(
                path == candidate
                or path in candidate.parents
                or candidate in path.parents
                for candidate in changed
            )

        errors = [
            dict(item)
            for item in previous_state.get("resource_errors") or []
            if isinstance(item, dict) and not affected(item)
        ]
        errors.extend(
            dict(item)
            for item in current_state.get("resource_errors") or []
            if isinstance(item, dict)
        )
        deduplicated: list[dict[str, Any]] = []
        seen: set[str] = set()
        for error in errors:
            key = json.dumps(error, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(error)
        return deduplicated

    @staticmethod
    def _retry_incremental_paths(
        adapter_name: str,
        incremental_scan: Callable[..., ChangeSet | None],
        paths: tuple[str, ...],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
    ) -> ChangeSet | None:
        previous_by_path = {
            stripped_text((record.get("source") or {}).get("path")): stripped_text(
                record.get("resource_id")
            )
            for record in previous_records
            if stripped_text((record.get("source") or {}).get("path"))
        }
        upserts: dict[str, dict[str, Any]] = {}
        deletes: set[str] = set()
        source_state: dict[str, Any] = {}
        resource_errors: list[dict[str, str]] = []
        for path in paths:
            try:
                changes = incremental_scan(
                    (path,),
                    previous_records,
                    previous_state,
                )
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": previous_by_path.get(path, ""),
                        "path": path,
                        "error": str(exc)[:1000],
                    }
                )
                continue
            if changes is None:
                return None
            if not isinstance(changes, ChangeSet) or changes.adapter != adapter_name:
                raise ValueError(
                    f"adapter {adapter_name} returned an invalid incremental ChangeSet"
                )
            resource_errors.extend(
                dict(item)
                for item in changes.source_state.get("resource_errors") or []
                if isinstance(item, dict)
            )
            source_state.update(
                {
                    key: value
                    for key, value in changes.source_state.items()
                    if key != "resource_errors"
                }
            )
            for resource_id in changes.deletes:
                clean_id = stripped_text(resource_id)
                if clean_id:
                    deletes.add(clean_id)
                    upserts.pop(clean_id, None)
            for record in changes.upserts:
                resource_id = stripped_text(record.get("resource_id"))
                if resource_id:
                    upserts[resource_id] = record
                    deletes.discard(resource_id)
        return ChangeSet(
            adapter=adapter_name,
            upserts=tuple(upserts[key] for key in sorted(upserts)),
            deletes=tuple(sorted(deletes)),
            source_state={
                **source_state,
                "last_incremental_path_count": len(paths),
                "resource_errors": resource_errors,
            },
        )

    def _run(self) -> None:
        # Every process generation checks source fingerprints once. Existing
        # snapshots avoid parsing when unchanged, while edits made during
        # downtime are recovered immediately instead of waiting for the
        # periodic reconcile.
        self.request_refresh(self.adapters, force=False)
        # The reconcile runs on a clock, not only after an idle spell: while
        # agents keep editing knowledge, the queue is never empty, and a change
        # the file watch missed (new Residents, 2026-10-03) stayed unindexed
        # for as long as the edits went on.
        next_reconcile = time.monotonic() + self._reconcile_seconds
        while not self._stop_event.is_set():
            try:
                name = self._queue.get(
                    timeout=max(0.0, next_reconcile - time.monotonic())
                )
            except queue.Empty:
                name = None
            if time.monotonic() >= next_reconcile:
                next_reconcile = time.monotonic() + self._reconcile_seconds
                with self._condition:
                    idle = [
                        adapter for adapter in self.adapters
                        if adapter not in self._pending and adapter not in self._running
                    ]
                if idle:
                    self.request_refresh(idle, force=False)
            if name is None:
                continue
            if not name and self._stop_event.is_set():
                self._queue.task_done()
                break
            with self._condition:
                # A fixed window from the first change merges filesystem and
                # explicit notifications without starvation under steady writes.
                while not self._stop_event.is_set() and name in self._pending:
                    remaining = self._pending[name].queued_at + self._merge_seconds - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                if self._stop_event.is_set():
                    self._queue.task_done()
                    break
                pending = self._pending.pop(name, _PendingRefresh(full=True))
                self._running.add(name)
            started = time.monotonic()
            result: dict[str, Any] | None = None
            try:
                with cancellable_scan(self._stop_event):
                    result = self.refresh_now(
                        name,
                        force=pending.force,
                        rescan=pending.rescan,
                        paths=() if pending.full else pending.paths,
                        joined_paths=pending.paths if pending.full else (),
                        _claimed=True,
                    )
            except ScanCancelled:
                pass
            except Exception:
                # refresh_now already recorded the error in the adapter status.
                LOGGER.exception("resource index refresh failed: %s", name)
            finally:
                # One worker serves every adapter, so a slow refresh delays the
                # rest: say how long this one waited in the queue and ran.
                waited = started - pending.queued_at if pending.queued_at else 0.0
                ran = time.monotonic() - started
                timing.report_slow(
                    "resource index refresh",
                    max(waited, ran),
                    threshold=SLOW_REFRESH_SECONDS,
                    adapter=name,
                    waited=round(waited, 3),
                    ran=round(ran, 3),
                    paths=0 if pending.full else len(pending.paths),
                    queued=self._queue.qsize(),
                )
                # Worker refreshes batch writes in memory. Persist them before
                # announcing the new projection to Portal.
                ok = False
                try:
                    self.store.flush()
                    if result is not None:
                        self._completed_refresh(name, result, forced=pending.force, emit=True)
                        ok = not stripped_text((self.store.status().get("adapters", {}).get(name) or {}).get("last_error"))
                except Exception:
                    LOGGER.exception("resource index persistence failed: %s", name)
                finally:
                    self._queue.task_done()
                    with self._condition:
                        pending.completion.ok = ok
                        pending.completion.done = True
                        self._running.discard(name)
                        self._condition.notify_all()

    def wait(self, adapters: Iterable[str] = (), timeout: float = 90.0) -> bool:
        names = set(adapters) or set(self.adapters)
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            # Capture the batches visible at entry. Later changes must not
            # extend this caller's barrier indefinitely.
            completions = [self._latest_refresh[name] for name in names if name in self._latest_refresh]
            while any(not item.done for item in completions):
                if self._stop_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        if self._stop_event.is_set():
            return False
        if not all(item.ok for item in completions):
            return False
        status = self.store.status().get("adapters") or {}
        return all(
            not stripped_text((status.get(name) or {}).get("last_error"))
            for name in names if name not in self._latest_refresh
        )

    def status(self) -> dict[str, Any]:
        with self._condition:
            status = self.store.status(self._running, self._pending)
        status["file_detector"] = (
            self._file_detector.status()
            if self._file_detector is not None
            else {
                "enabled": False,
                "ready": False,
                "watch_count": 0,
                "last_event_at": "",
                "last_error": "",
            }
        )
        return status


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


def _project_statuses(projects: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {
        stripped_text(item.get("id")): stripped_text(item.get("status"))
        for item in projects
        if isinstance(item, dict) and stripped_text(item.get("id"))
    }


class ProjectKnowledgeAdapter(_PathsAdapter):
    name = "project_knowledge"
    resource_types = frozenset(
        {
            "meeting",
            "question",
            "idea",
            "insight",
            "decision",
            "plan",
            "experiment",
            "thinking",
            "risk",
            "concept",
            "information",
            "patent",
            "material",
            "review",
            "todo",
        }
    )
    roots = (
        Path(str(portal_path("share_root") / "Projects")),
    )
    registry_collections = frozenset({project_registry.COLLECTION})
    protect_existing_sources = True

    def project_record(self, resource_type: str, item: dict[str, Any]) -> dict[str, Any]:
        """Read only the newly registered entry when publishing its summary."""
        if resource_type not in self.resource_types:
            raise ValueError(f"unsupported Knowledge resource type: {resource_type}")
        source_path = Path(stripped_text(item.get("path"))).resolve(strict=False)
        project_id = stripped_text(item.get("project") or next(iter(item.get("project_ids") or []), ""))
        if not source_path.is_file() or not project_id:
            raise ValueError("Knowledge registration requires its source path and Project")
        content_root = Path(stripped_text(item.get("content_root")) or str(source_path.parent))
        raw = self._raw_item_for_existing_record({
            "resource_id": item.get("resource_id"),
            "source": {"path": str(source_path)},
            "attributes": {"content_root": str(content_root), "project_ids": [project_id]},
        })
        if not raw:
            raise RuntimeError(f"could not parse registered Knowledge resource: {item.get('resource_id')}")
        statuses = _project_statuses(knowledge_backend._parse_projects_yaml())
        result = self._record_for_item(raw, statuses=statuses, order=0)
        if result is None:
            raise RuntimeError("registered Knowledge resource has no index summary")
        return result

    def source_scope(self) -> dict[str, Any]:
        """The sources the change finder lists: each knowledge base's
        directories two levels down and the item files in them."""
        extensions = sorted(knowledge_backend._VALID_ITEM_FILE_EXTENSIONS)
        trees: list[dict[str, Any]] = [
            {
                "root": str(_resolved_root(root)),
                "max_depth": 2,
                "files": ["*" + extension for extension in extensions],
            }
            for root in sorted(Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base"))
        ]
        return {"trees": trees, "files": []}

    @staticmethod
    def source_resolution_paths(records: Iterable[dict[str, Any]]) -> list[str]:
        paths: set[str] = set()
        for record in records:
            source = record.get("source") if isinstance(record.get("source"), dict) else {}
            attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            for value in (source.get("path"), attrs.get("content_root"), attrs.get("canonical_root")):
                if stripped_text(value):
                    paths.add(stripped_text(value))
        return sorted(paths)

    @staticmethod
    def _scope_index(
        records: list[dict[str, Any]],
    ) -> Callable[[Path], list[dict[str, Any]]]:
        """Find the records whose path scope holds a path, as
        knowledge_backend._indexed_knowledge_record_owns_path does, resolving
        each record's paths once instead of once per changed path."""
        by_source: dict[Path, list[int]] = {}
        by_root: dict[Path, list[int]] = {}
        for index, record in enumerate(records):
            source = record.get("source") if isinstance(record.get("source"), dict) else {}
            attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
            source_text = stripped_text(source.get("path"))
            if source_text:
                by_source.setdefault(_known_path(source_text), []).append(index)
            if stripped_text(record.get("resource_type")) == "article":
                root_text = stripped_text(attrs.get("canonical_root"))
                if root_text:
                    by_root.setdefault(_known_path(root_text), []).append(index)
                continue
            root_text = stripped_text(attrs.get("content_root"))
            if attrs.get("single_file") or not root_text:
                continue
            root = _known_path(root_text)
            if root.parent.name == "knowledge_base":
                resource_id = stripped_text(record.get("resource_id"))
                if not resource_id:
                    continue
                root = root / resource_id
            by_root.setdefault(root, []).append(index)

        def matching(path: Path) -> list[dict[str, Any]]:
            found = set(by_source.get(path, ()))
            for candidate in (path, *path.parents):
                found.update(by_root.get(candidate, ()))
            return [records[index] for index in sorted(found)]

        return matching

    def watch_directories(self) -> list[Path]:
        directories: set[Path] = set()
        for root in Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base"):
            directories.update(_watch_tree(root, max_depth=2))
        return sorted(directories, key=str)

    def owns_path(self, path: Path) -> bool:
        resolved = _resolved_path(path)
        if resolved == _resolved_root(self.roots[0]):
            return True
        parts = resolved.parts
        if "knowledge_base" not in parts:
            return False
        index = parts.index("knowledge_base")
        if index + 1 >= len(parts):
            return True
        _allowed, folder_map, _reverse, _hash = knowledge_backend._get_tag_config()
        knowledge_folders = set(folder_map.values()) - {"skill"}
        return parts[index + 1] in knowledge_folders

    def protects_missing_source(
        self,
        record: dict[str, Any],
        source_path: Path,
    ) -> bool:
        parts = source_path.parts
        if "knowledge_base" not in parts:
            return False
        index = parts.index("knowledge_base")
        if index + 1 >= len(parts):
            return False
        tag_root = Path(*parts[: index + 2])
        _allowed, _folder_map, folder_to_type, _hash = knowledge_backend._get_tag_config()
        item_type = folder_to_type.get(
            parts[index + 1],
            stripped_text(record.get("resource_type")),
        )
        return any(
            source_path == primary_path.resolve(strict=False)
            for _entry_root, primary_path, _entry_files in knowledge_backend._entry_specs(
                tag_root,
                item_type,
            )
        )

    def _record_for_item(
        self,
        raw: dict[str, Any],
        *,
        statuses: dict[str, str],
        order: int,
    ) -> dict[str, Any] | None:
        item = knowledge_backend.public_item_record(raw)
        resource_id = stripped_text(item.get("kb_id"))
        resource_type = stripped_text(item.get("type"))
        if resource_type == "skill":
            return None
        project = stripped_text(item.get("project"))
        root_path = Path(stripped_text(raw.get("root_path")))
        relative_path = stripped_text(raw.get("relative_path") or raw.get("filename"))
        source_path = root_path / relative_path if relative_path else root_path
        facets = ["knowledge"]
        if resource_type == "experiment":
            facets.append("explore")
        attributes = {
            key: value
            for key, value in item.items()
            if key
            in {
                "type",
                "created_at",
                "version",
                "status",
                "importance",
                "metaness",
                "ai_generated",
                "assignees",
                "task_kind",
                "finished_at",
                "archived_at",
                "filename",
                "mtime",
                "file_count",
                "file_count_truncated",
                "source",
                "source_label",
                "label",
                "kind",
            }
        }
        attributes.update(
            {
                "project_ids": [project] if project else [],
                "relations": [resource_relation("related", target) for target in item.get("related") or []],
                "project_status": statuses.get(project, ""),
                "preview": stripped_text(item.get("preview")),
                "content_root": str(root_path),
                "sort_order": order,
            }
        )
        return resource_record(
            resource_id=resource_id,
            resource_type=resource_type,
            title=item.get("title") or item.get("filename") or resource_id,
            summary=item.get("preview"),
            adapter=self.name,
            path=source_path,
            modified_at=_timestamp_iso(item.get("mtime")),
            facets=facets,
            attributes=attributes,
            search_terms=[
                item.get("filename"),
                project,
                resource_type,
                item.get("source"),
                item.get("source_label"),
                item.get("label"),
            ],
        )


    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = knowledge_backend.scan_index_source(active_only=False)
        projects = [dict(item) for item in payload.get("projects") or [] if isinstance(item, dict)]
        statuses = _project_statuses(projects)
        records: list[dict[str, Any]] = []
        for order, raw in enumerate(payload.get("items") or []):
            if not isinstance(raw, dict):
                continue
            record = self._record_for_item(
                raw,
                statuses=statuses,
                order=order,
            )
            if record:
                records.append(record)

        return records, {
            "allowed_types": [
                value
                for value in payload.get("allowed_types") or []
                if value != "skill"
            ],
            "projects": projects,
            "resource_errors": list(payload.get("resource_errors") or []),
        }

    @staticmethod
    def _raw_item_for_existing_record(record: dict[str, Any]) -> dict[str, Any]:
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        attrs = record.get("attributes") if isinstance(record.get("attributes"), dict) else {}
        source_path = Path(stripped_text(source.get("path"))).resolve(strict=False)
        content_root = Path(stripped_text(attrs.get("content_root"))).resolve(strict=False)
        if not source_path.is_file():
            return {}
        if content_root.parent.name == "knowledge_base":
            files_root = content_root / stripped_text(record.get("resource_id"))
        else:
            files_root = content_root
        entry_files, truncated = knowledge_backend._bounded_entry_files(source_path, files_root)
        allowed, _folder_map, folder_to_type, config_hash = knowledge_backend._get_tag_config()
        return ProjectKnowledgeAdapter._indexed_item(
            knowledge_backend._get_md_item_cached(
                source_path,
                content_root,
                entry_files,
                stripped_text(next(iter(attrs.get("project_ids") or []), "")),
                config_hash,
                set(allowed),
                folder_to_type,
            ),
            entry_files,
            truncated,
        )

    @staticmethod
    def _indexed_item(item: dict[str, Any], entry_files: list[Path], truncated: bool) -> dict[str, Any]:
        # The same fields knowledge_backend.scan_index_source gives a full scan's items.
        if not item:
            return {}
        item.pop("files", None)
        item.pop("path", None)
        item["file_count"] = len(entry_files)
        item["file_count_truncated"] = truncated
        return item

    @staticmethod
    def _raw_item_for_new_path(path: Path, projects: Iterable[dict[str, Any]]) -> dict[str, Any]:
        allowed, _folder_map, folder_to_type, config_hash = knowledge_backend._get_tag_config()
        allowed_types = set(allowed)
        for project in projects:
            project_id = stripped_text(project.get("id"))
            project_path = stripped_text(project.get("path"))
            if not project_id or not project_path:
                continue
            knowledge_root = (Path(project_path).expanduser() / "knowledge_base").resolve(strict=False)
            try:
                relative = path.relative_to(knowledge_root)
            except ValueError:
                continue
            if len(relative.parts) < 2:
                return {}
            folder = relative.parts[0]
            item_type = folder_to_type.get(folder, folder)
            if item_type not in allowed_types:
                return {}
            tag_root = knowledge_root / folder
            for entry_root, primary_path, entry_files, truncated in knowledge_backend._index_entry_specs(tag_root):
                if entry_root == tag_root:
                    text = knowledge_backend._read_file_text(str(primary_path))
                    meta, _body = knowledge_backend._parse_front_matter(text)
                    resource_id = knowledge_backend.knowledge_metadata_id(meta)
                    companion_root = tag_root / resource_id if resource_id else None
                    in_scope = path == primary_path or bool(
                        companion_root
                        and (path == companion_root or companion_root in path.parents)
                    )
                else:
                    in_scope = path == entry_root or entry_root in path.parents
                if not in_scope:
                    continue
                return ProjectKnowledgeAdapter._indexed_item(
                    knowledge_backend._get_md_item_cached(
                        primary_path,
                        entry_root,
                        entry_files,
                        project_id,
                        config_hash,
                        allowed_types,
                        folder_to_type,
                    ),
                    entry_files,
                    truncated,
                )
        return {}

    def scan_changes(
        self,
        paths: Iterable[str],
        previous: Iterable[dict[str, Any]],
        source_state: dict[str, Any],
    ) -> ChangeSet | None:
        paths = tuple(paths)
        if project_registry.CHANGE_PATH in paths:
            return None
        changed_paths = tuple(
            Path(path).expanduser().resolve(strict=False) for path in paths
        )
        projects = [dict(item) for item in knowledge_backend._parse_projects_yaml()]
        statuses = _project_statuses(projects)
        previous_records = list(previous)
        records_in_scope = self._scope_index(previous_records)
        affected: dict[str, dict[str, Any]] = {}
        new_paths: list[Path] = []
        for path in changed_paths:
            matching = records_in_scope(path)
            if matching:
                for record in matching:
                    affected[stripped_text(record.get("resource_id"))] = record
            else:
                new_paths.append(path)

        upserts: list[dict[str, Any]] = []
        deletes: list[str] = []
        for resource_id, previous_record in affected.items():
            source = previous_record.get("source") if isinstance(previous_record.get("source"), dict) else {}
            if not Path(stripped_text(source.get("path"))).is_file():
                deletes.append(resource_id)
                continue
            raw = self._raw_item_for_existing_record(previous_record)
            if not raw:
                raise RuntimeError(f"could not parse changed Knowledge resource: {resource_id}")
            order = int((previous_record.get("attributes") or {}).get("sort_order") or 0)
            record = self._record_for_item(
                raw,
                statuses=statuses,
                order=order,
            )
            if record:
                upserts.append(record)
                if record["resource_id"] != resource_id:
                    deletes.append(resource_id)

        next_order = 1 + max(
            [
                int((record.get("attributes") or {}).get("sort_order") or 0)
                for record in previous_records
            ]
            or [0]
        )
        seen_new_ids = {record["resource_id"] for record in upserts}
        for path in new_paths:
            raw = self._raw_item_for_new_path(path, projects)
            if not raw:
                continue
            record = self._record_for_item(
                raw,
                statuses=statuses,
                order=next_order,
            )
            if record and record["resource_id"] not in seen_new_ids:
                upserts.append(record)
                seen_new_ids.add(record["resource_id"])
                next_order += 1
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts),
            deletes=tuple(_dedupe(deletes)),
            source_state={
                **source_state,
                "last_incremental_path_count": len(changed_paths),
            },
        )


class SkillPackageAdapter(_PathsAdapter):
    name = "skill"
    resource_types = frozenset({"skill"})
    roots = (
        Path(str(portal_path("share_root") / "Skills")),
        Path(str(portal_path("share_root") / "Projects")),
    )
    registry_collections = frozenset({project_registry.COLLECTION})
    recursive_fingerprint = True
    protect_existing_sources = True

    def source_paths(self) -> list[Path]:
        project_roots = sorted(Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base/skill"))
        roots = [
            Path(str(portal_path("share_root") / "Skills")),
            *project_roots,
        ]
        return _tree_paths(roots)

    def fingerprint(self) -> str:
        return self._fingerprint_paths(self.source_paths())

    def watch_directories(self) -> list[Path]:
        directories = set(_watch_tree(Path(str(portal_path("share_root") / "Skills")), max_depth=1))
        for root in Path(str(portal_path("share_root") / "Projects")).glob("*/knowledge_base/skill"):
            directories.update(_watch_tree(root, max_depth=1))
        return sorted(directories, key=str)

    def owns_path(self, path: Path) -> bool:
        resolved = _resolved_path(path)
        shared_root = _resolved_root(str(portal_path("share_root") / "Skills"))
        if resolved == shared_root or shared_root in resolved.parents:
            return True
        parts = resolved.parts
        return "knowledge_base" in parts and "skill" in parts[parts.index("knowledge_base") + 1 :]

    def protects_missing_source(
        self,
        _record: dict[str, Any],
        source_path: Path,
    ) -> bool:
        return self.owns_path(source_path)

    def _record_for_item(
        self,
        raw: dict[str, Any],
        *,
        statuses: dict[str, str],
        order: int,
    ) -> dict[str, Any]:
        skill_id = stripped_text(raw.get("skill_id"))
        raw_root_path = Path(stripped_text(raw.get("root_path")))
        relative_path = stripped_text(raw.get("relative_path") or raw.get("filename") or "SKILL.md")
        source_path = raw_root_path if raw_root_path.is_file() else raw_root_path / relative_path
        root_path = raw_root_path.parent if raw_root_path.is_file() else raw_root_path
        files: list[dict[str, Any]] = []
        for file in raw.get("files") or []:
            if not isinstance(file, dict):
                continue
            relative = stripped_text(file.get("relative_path") or file.get("name"))
            primary = bool(file.get("primary")) or relative == relative_path
            files.append(
                {
                    "address": skill_id if primary else knowledge_backend._nested_file_address(skill_id, relative),
                    "name": stripped_text(file.get("name") or relative),
                    "relative_path": relative,
                    "kind": stripped_text(file.get("kind") or "binary"),
                    "primary": primary,
                }
            )
        source_kind = stripped_text(raw.get("source"))
        project = "" if source_kind in {"shared", "third_party"} else stripped_text(raw.get("project"))
        project_status = statuses.get(project, "")
        attributes = {
            "type": "skill",
            "project_ids": [project] if project else [],
            "read_scope": (
                "global"
                if source_kind in {"shared", "third_party"}
                else "project"
            ),
            "project_status": project_status,
            "status": stripped_text(raw.get("status")),
            "created_at": stripped_text(raw.get("created_at")),
            "importance": int(raw.get("importance") or 0),
            "mtime": int(raw.get("mtime") or 0),
            "filename": stripped_text(raw.get("filename") or "SKILL.md"),
            "relative_path": relative_path,
            "content_root": str(root_path),
            "single_file": raw_root_path.is_file(),
            "file_count": int(raw.get("file_count") or len(files)),
            "name": stripped_text(raw.get("name")),
            "source": source_kind,
            "source_label": stripped_text(raw.get("source_label")),
            "label": stripped_text(raw.get("label")),
            "preview": stripped_text(raw.get("preview") or raw.get("description")),
            "ai_generated": False,
            "sort_order": order,
        }
        search_terms: list[object] = [
            raw.get("name"),
            raw.get("description"),
            raw.get("filename"),
            relative_path,
            project,
            raw.get("source"),
            raw.get("source_label"),
            raw.get("label"),
        ]
        for file in files:
            search_terms.extend([file.get("name"), file.get("relative_path")])
        return resource_record(
            resource_id=skill_id,
            resource_type="skill",
            title=raw.get("title") or raw.get("name") or skill_id,
            summary=raw.get("description") or raw.get("preview"),
            adapter=self.name,
            path=source_path,
            modified_at=_timestamp_iso(raw.get("mtime")),
            facets=["skill", "knowledge"],
            attributes=attributes,
            search_terms=search_terms,
        )

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = skill_backend.scan_index_source(active_only=False)
        statuses = _project_statuses(payload.get("projects") or [])
        records = [
            self._record_for_item(raw, statuses=statuses, order=order)
            for order, raw in enumerate(payload.get("items") or [])
            if isinstance(raw, dict)
        ]
        return records, {
            "projects": list(payload.get("projects") or []),
            "sources": list(payload.get("sources") or []),
            "resource_errors": list(payload.get("resource_errors") or []),
        }

    @staticmethod
    def _record_scope_contains(record: dict[str, Any], path: Path) -> bool:
        return knowledge_backend._indexed_knowledge_record_owns_path(record, path)

    @staticmethod
    def _raw_item(
        root: Path,
        *,
        project: str,
        source: str,
        source_label: str,
        preferred_primary: Path | None = None,
    ) -> dict[str, Any]:
        markdown_files = skill_backend._candidate_markdown_files(root)
        primary = (
            preferred_primary
            if preferred_primary and preferred_primary in markdown_files
            else skill_backend._choose_primary_file(root, markdown_files)
        )
        if not primary:
            return {}
        files = skill_backend._candidate_skill_files(root) or markdown_files
        return skill_backend._get_item_cached(
            root=root,
            primary_path=primary,
            files=files,
            project=project,
            source=source,
            source_label=source_label,
        )

    @staticmethod
    def _new_item_context(
        path: Path,
        projects: Iterable[dict[str, Any]],
    ) -> tuple[Path, str, str, str] | None:
        shared_root = skill_backend.SHARED_SKILLS_ROOT.resolve(strict=False)
        third_party_root = skill_backend.THIRD_PARTY_SKILLS_ROOT.resolve(strict=False)
        try:
            relative = path.relative_to(third_party_root)
        except ValueError:
            relative = None
        if relative and relative.parts and not relative.parts[0].startswith("."):
            root = third_party_root / relative.parts[0]
            return root, "", "third_party", "Third-party"
        try:
            relative = path.relative_to(shared_root)
        except ValueError:
            relative = None
        if (
            relative
            and relative.parts
            and not relative.parts[0].startswith(".")
            and relative.parts[0] != third_party_root.name
        ):
            root = shared_root / relative.parts[0]
            return root, "", "shared", "Shared"
        for project in projects:
            project_id = stripped_text(project.get("id"))
            project_path = stripped_text(project.get("path"))
            if not project_id or not project_path:
                continue
            skill_root = (Path(project_path).expanduser() / "knowledge_base" / "skill").resolve(strict=False)
            try:
                relative = path.relative_to(skill_root)
            except ValueError:
                continue
            if not relative.parts or relative.parts[0].startswith("."):
                return None
            return skill_root / relative.parts[0], project_id, "project", "Project"
        return None

    def scan_changes(
        self,
        paths: Iterable[str],
        previous: Iterable[dict[str, Any]],
        _source_state: dict[str, Any],
    ) -> ChangeSet | None:
        paths = tuple(paths)
        if project_registry.CHANGE_PATH in paths:
            return None
        changed_paths = tuple(
            Path(path).expanduser().resolve(strict=False) for path in paths
        )
        projects = [dict(item) for item in skill_backend._parse_projects(active_only=False)]
        statuses = _project_statuses(projects)
        previous_records = list(previous)
        affected: dict[str, dict[str, Any]] = {}
        new_paths: list[Path] = []
        for path in changed_paths:
            matches = [
                record
                for record in previous_records
                if self._record_scope_contains(record, path)
            ]
            if matches:
                for record in matches:
                    affected[stripped_text(record.get("resource_id"))] = record
            else:
                new_paths.append(path)

        upserts: list[dict[str, Any]] = []
        deletes: list[str] = []
        for resource_id, previous_record in affected.items():
            source = previous_record.get("source") if isinstance(previous_record.get("source"), dict) else {}
            attrs = previous_record.get("attributes") if isinstance(previous_record.get("attributes"), dict) else {}
            source_path = Path(stripped_text(source.get("path"))).resolve(strict=False)
            root = source_path if attrs.get("single_file") else Path(stripped_text(attrs.get("content_root"))).resolve(strict=False)
            raw = self._raw_item(
                root,
                project=stripped_text(next(iter(attrs.get("project_ids") or []), "")),
                source=stripped_text(attrs.get("source")),
                source_label=stripped_text(attrs.get("source_label")),
                preferred_primary=source_path,
            )
            if not raw:
                if source_path.is_file():
                    raise RuntimeError(f"could not parse changed Skill resource: {resource_id}")
                deletes.append(resource_id)
                continue
            order = int(attrs.get("sort_order") or 0)
            record = self._record_for_item(raw, statuses=statuses, order=order)
            upserts.append(record)
            if record["resource_id"] != resource_id:
                deletes.append(resource_id)

        next_order = 1 + max(
            [
                int((record.get("attributes") or {}).get("sort_order") or 0)
                for record in previous_records
            ]
            or [0]
        )
        seen_new_ids = {record["resource_id"] for record in upserts}
        for path in new_paths:
            context = self._new_item_context(path, projects)
            if not context:
                continue
            root, project, source, source_label = context
            raw = self._raw_item(
                root,
                project=project,
                source=source,
                source_label=source_label,
            )
            if not raw:
                continue
            record = self._record_for_item(raw, statuses=statuses, order=next_order)
            if record["resource_id"] in seen_new_ids:
                continue
            upserts.append(record)
            seen_new_ids.add(record["resource_id"])
            next_order += 1
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts),
            deletes=tuple(_dedupe(deletes)),
            source_state={"last_incremental_path_count": len(changed_paths)},
        )


class ProjectRegistryAdapter(_PathsAdapter):
    name = "project"
    resource_types = frozenset({"project"})
    registry_collections = frozenset({project_registry.COLLECTION})

    def __init__(self) -> None:
        self.roots = ()

    def watch_directories(self) -> list[Path]:
        return []

    def owns_path(self, path: Path) -> bool:
        return False

    @staticmethod
    def change_key(resource_type: str, resource_id: str) -> str:
        from management_portal.resources.registry_store import registry_change_key
        return registry_change_key(project_registry.COLLECTION, resource_id)

    def project_record(self, resource_type: str, item: dict[str, Any]) -> dict[str, Any]:
        # Registration wraps the committed fields in ``project``; a registry
        # scan supplies them directly. Both must publish the same path before
        # SocietyService initializes the Project's principal.
        if isinstance(item.get("project"), dict):
            item = {**item["project"], "resource_id": item.get("resource_id")}
        # The Project ID is its title: a Project has no other name.
        project_id = stripped_text(item.get("resource_id") or item.get("id"))
        return resource_record(
            resource_id=project_id, resource_type="project",
            title=project_id, summary=item.get("description"),
            adapter=self.name, facets=["project"],
            attributes={
                "status": stripped_text(item.get("status")),
                "project_type": stripped_text(item.get("project_type")), "path": stripped_text(item.get("path")),
                "ddl": stripped_text(item.get("ddl")), "tags": list(item.get("tags") or []),
            },
            search_terms=[item.get("status"), item.get("path"), *(item.get("tags") or [])],
        )

    def scan_changes(self, paths, previous_records, previous_state) -> ChangeSet:
        del previous_state
        upserts = []
        deletes = []
        changes = {change for change in map(parse_registry_change_key, paths)
                   if change is not None and change[0] == project_registry.COLLECTION}
        if (project_registry.COLLECTION, project_registry.DOCUMENT_ID) in changes:
            current = {
                stripped_text(item.get("id") or item.get("name")): self.project_record("project", item)
                for item in project_provider.load_projects_payload().get("projects") or []
                if isinstance(item, dict)
            }
            previous = {record["resource_id"]: record for record in previous_records}
            return ChangeSet(
                self.name,
                tuple(record for resource_id, record in current.items()
                      if previous.get(resource_id) != record),
                tuple(sorted(set(previous) - set(current))),
            )
        for path in paths:
            change = parse_registry_change_key(path)
            if change is None or change[0] != project_registry.COLLECTION:
                continue
            resource_id = change[1]
            try:
                item = project_provider._project_record(resource_id)
            except KeyError:
                deletes.append(resource_id)
            else:
                upserts.append(self.project_record("project", item))
        return ChangeSet(self.name, tuple(upserts), tuple(deletes))

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = project_provider.load_projects_payload()
        records = []
        project_ids = []
        resource_errors: list[dict[str, str]] = []
        for item in payload.get("projects") or []:
            if not isinstance(item, dict):
                continue
            project_id = stripped_text(item.get("id") or item.get("name"))
            try:
                record = self.project_record("project", item)
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": project_id,
                        "error": str(exc)[:1000],
                    }
                )
                continue
            project_ids.append(project_id)
            records.append(record)
        return records, {
            "project_count": len(records),
            "project_ids": project_ids,
            "schema_version": payload["schema_version"],
            "status_vocab": list(payload.get("status_vocab") or []),
            "tag_vocab": list(payload.get("tag_vocab") or []),
            "resource_errors": resource_errors,
        }


class CollaborationRegistryAdapter(_PathsAdapter):
    """Index Resident/Lineage descriptors and the canonical Workspace table."""

    name = "collaboration"
    resource_types = frozenset({"resident", "lineage", "workspace", "family"})
    registry_collections = frozenset({"resident", "lineage", "family"})
    _TYPES = ("resident", "lineage", "workspace", "family")

    def __init__(self) -> None:
        self.roots = ()

    def source_paths(self) -> list[Path]:
        return []

    def fingerprint(self) -> str:
        from management_portal.resources.workspace_store import get_workspace_store
        store = get_workspace_store()
        with store.database.read() as connection:
            revision = connection.execute(f"SELECT count(*) AS n, max(updated_at) AS updated FROM {store.table}").fetchone()
        return hashlib.sha256((_CURRENT_PROJECTION_CODE_FINGERPRINT + "\0" +
            self._registry_fingerprint() + json.dumps({"n": revision["n"], "updated": iso_time(revision["updated"])},
                                                     sort_keys=True)).encode()).hexdigest()

    def watch_directories(self) -> list[Path]:
        return []

    def owns_path(self, path: Path) -> bool:
        return False

    def _index_record(
        self,
        item: dict[str, Any],
        store: CollaborationDescriptorStore,
    ) -> dict[str, Any]:
        resource_id = stripped_text(item.get("resource_id"))
        resource_type = RESOURCE_IDENTITY.parse(resource_id).resource_type
        if resource_type not in self.resource_types:
            raise ValueError(
                f"unsupported collaboration resource type: {resource_type}"
            )
        return descriptor_resource_record(item)

    def scan_changes(
        self,
        paths: Iterable[str],
        previous_records: list[dict[str, Any]],
        previous_state: dict[str, Any],
    ) -> ChangeSet:
        store = CollaborationDescriptorStore()
        upserts: dict[str, dict[str, Any]] = {}
        deletes: set[str] = set()
        changed_ids = {
            change[1]
            for change in map(parse_registry_change_key, paths)
            if change is not None and change[0] in self.registry_collections
        }
        for resource_id in sorted(changed_ids):
            try:
                resource_type = RESOURCE_IDENTITY.parse(resource_id).resource_type
            except ValueError:
                continue
            if resource_type not in self.resource_types:
                continue
            try:
                item = store.get(resource_id)
            except KeyError:
                deletes.add(resource_id)
                continue
            upserts[resource_id] = self._index_record(item, store)

        next_types = {
            stripped_text(record.get("resource_id")): stripped_text(record.get("resource_type"))
            for record in previous_records
        }
        for resource_id in deletes:
            next_types.pop(resource_id, None)
        for resource_id, record in upserts.items():
            next_types[resource_id] = stripped_text(record.get("resource_type"))
        counts = {
            resource_type: sum(
                1 for value in next_types.values() if value == resource_type
            )
            for resource_type in sorted(self.resource_types)
        }
        return ChangeSet(
            adapter=self.name,
            upserts=tuple(upserts[key] for key in sorted(upserts)),
            deletes=tuple(sorted(deletes)),
            source_state={
                "counts": counts,
                "last_incremental_path_count": len(changed_ids),
            },
        )

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        resource_errors: list[dict[str, str]] = []
        store = CollaborationDescriptorStore()
        for resource_type in self._TYPES:
            count = 0
            for item in store.list(resource_type):
                try:
                    records.append(self._index_record(item, store))
                    count += 1
                except Exception as exc:
                    resource_errors.append(
                        {
                            "resource_id": stripped_text(item.get("resource_id")),
                            "error": str(exc)[:1000],
                        }
                    )
            counts[resource_type] = count
        return records, {
            "counts": counts,
            "resource_errors": resource_errors,
        }


class _RepoRegistryProjection:
    resource_type = "repo"
    collection = resource_identity_backend.REPO_COLLECTION

    def validate(self, resource_id, raw):
        return resource_identity_backend._validated_repo(resource_id, raw)

    def project(self, item, *, adapter):
        resource_id = stripped_text(item.get("resource_id") or item.get("id"))
        projects = [stripped_text(value) for value in item.get("project_ids") or [] if stripped_text(value)]
        checkouts = [value for value in item.get("checkouts") or [] if isinstance(value, dict)]
        remotes = [value for value in item.get("remotes") or [] if isinstance(value, dict)]
        return resource_record(
            resource_id=resource_id, resource_type="repo",
            title=item.get("name") or resource_id, summary=item.get("description"),
            adapter=adapter, modified_at=item.get("updated_at") or item.get("created_at"),
            facets=["repo"],
            attributes={
                "project_ids": projects, "ownership": stripped_text(item.get("ownership")),
                "canonical_checkout": stripped_text(item.get("canonical_checkout")),
                "checkout_paths": [stripped_text(value.get("path")) for value in checkouts if stripped_text(value.get("path"))],
                "status": stripped_text(item.get("status")),
            },
            search_terms=[*projects, item.get("ownership"), item.get("canonical_checkout"),
                          *[stripped_text(value.get("path")) for value in checkouts],
                          *[stripped_text(value.get("url")) for value in remotes]],
        )


class ResourceRegistryAdapter(_PathsAdapter):
    name = "resource_registry"

    def __init__(self) -> None:
        self.roots = ()
        projections = [_RepoRegistryProjection(), *(
            projection for _feature, projection
            in portal_features.contributions("resource.registry_projection")
        )]
        self.projections = {source.resource_type: source for source in projections}
        self._collections = {source.collection: source for source in projections}
        if len(self.projections) != len(projections) or len(self._collections) != len(projections):
            raise ValueError("duplicate resource registry projection owner")
        self.resource_types = frozenset(self.projections)
        self.registry_collections = frozenset(self._collections)

    def source_paths(self) -> list[Path]:
        return []

    def fingerprint(self) -> str:
        digest = hashlib.sha256(
            (_CURRENT_PROJECTION_CODE_FINGERPRINT + "\0" + self._registry_fingerprint()).encode()
        )
        for source in self.projections.values():
            digest.update(source.resource_type.encode())
            digest.update(_adapter_projection_code_fingerprint(source).encode())
        return digest.hexdigest()

    def watch_directories(self) -> list[Path]:
        return []

    def owns_path(self, path: Path) -> bool:
        return False

    def change_key(self, resource_type: str, resource_id: str) -> str:
        from management_portal.resources.registry_store import registry_change_key
        return registry_change_key(self.projections[resource_type].collection, resource_id)

    def project_record(self, resource_type: str, item: dict[str, Any]) -> dict[str, Any]:
        source = self.projections.get(resource_type)
        if source is None:
            raise ValueError(f"unsupported registry resource type: {resource_type}")
        return source.project(item, adapter=self.name)

    def scan_changes(self, paths, previous_records, previous_state) -> ChangeSet:
        del previous_records, previous_state
        registry = get_registry_store()
        upserts = []
        deletes = []
        for path in paths:
            change = parse_registry_change_key(path)
            if change is None or change[0] not in self.registry_collections:
                continue
            collection, resource_id = change
            raw = registry.get(collection, resource_id)
            if raw is None:
                deletes.append(resource_id)
                continue
            source = self._collections[collection]
            item = source.validate(resource_id, raw)
            upserts.append(self.project_record(source.resource_type, item))
        return ChangeSet(self.name, tuple(upserts), tuple(deletes))

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records: list[dict[str, Any]] = []
        resource_errors: list[dict[str, str]] = []
        registry = get_registry_store()
        state: dict[str, Any] = {"resource_errors": resource_errors}
        for source in self.projections.values():
            items = []
            for resource_id, document in registry.documents(source.collection).items():
                try:
                    items.append(source.validate(resource_id, document))
                except Exception as exc:
                    resource_errors.append({"resource_id": resource_id, "error": str(exc)[:1000]})
            state[source.resource_type + "_count"] = len(items)
            for item in items:
                try:
                    records.append(self.project_record(source.resource_type, item))
                except Exception as exc:
                    resource_errors.append({"resource_id": stripped_text(item.get("id")), "error": str(exc)[:1000]})
        return records, state


class DeviceRegistryAdapter(_PathsAdapter):
    name = "device"
    resource_types = frozenset({"device"})
    registry_collections = frozenset({device_identity_backend.DEVICE_COLLECTION})
    # Device observations are a cache file written by the GPU monitor.
    roots = (device_identity_backend.DEFAULT_STATUS_PATH,)

    _GPU_OBSERVATION_FIELDS = (
        "state",
        "busy",
        "gpu_util_percent",
        "memory_supported",
        "memory_total_mb",
        "memory_used_mb",
        "memory_free_mb",
        "memory_util_percent",
        "unified_memory_total_mb",
        "unified_memory_used_mb",
        "unified_memory_used_percent",
        "temperature_c",
        "power_draw_w",
        "power_limit_w",
        "compute_processes",
        "graphics_processes",
    )
    _SERVER_OBSERVATION_FIELDS = (
        "gpu_count",
        "available_gpu_count",
        "busy_gpu_count",
        "empty_gpu_count",
        "idle_gpu_count",
        "busy_gpu_indexes",
        "stale",
        "refresh_error",
        "error",
    )

    @staticmethod
    def _observed_fields(
        observation: dict[str, Any],
        names: Iterable[str],
    ) -> dict[str, Any]:
        return {
            name: _json_value(observation[name])
            for name in names
            if name in observation
        }

    @staticmethod
    def change_key(resource_type: str, resource_id: str) -> str:
        from management_portal.resources.registry_store import registry_change_key
        return registry_change_key(device_identity_backend.DEVICE_COLLECTION, resource_id)

    def project_record(
        self, resource_type: str, device: dict[str, Any],
        *, observation: dict[str, Any] | None = None,
        child_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        device_id = stripped_text(device.get("resource_id") or device.get("device_id"))
        device_kind = stripped_text(device.get("device_kind"))
        network = device.get("network") if isinstance(device.get("network"), dict) else {}
        access_methods = device.get("access_methods") if isinstance(device.get("access_methods"), dict) else {}
        monitor = device.get("monitor") if isinstance(device.get("monitor"), dict) else {}
        observation = observation or {}
        observed_at = stripped_text(observation.get("updated_at"))
        parent_id = stripped_text(device.get("parent_device_id"))
        dynamic = self._observed_fields(
            observation,
            self._GPU_OBSERVATION_FIELDS if device_kind == "gpu" else self._SERVER_OBSERVATION_FIELDS,
        )
        runtime_status = stripped_text(observation.get("state") if device_kind == "gpu" else observation.get("status"))
        attributes = {
            "status": runtime_status or stripped_text(device.get("lifecycle")),
            "created_at": stripped_text(device.get("created_at")),
            "updated_at": observed_at or stripped_text(device.get("updated_at")),
            "relations": [resource_relation("parent", parent_id)] if parent_id else [],
            "device_kind": device_kind, "lifecycle": stripped_text(device.get("lifecycle")),
            "hostname": stripped_text(device.get("hostname")), "location": stripped_text(device.get("location")),
            "ip": stripped_text(network.get("primary_ip")),
            "mac_addresses": list(network.get("mac_addresses") or []),
            "access_methods": _json_value(access_methods),
            "parent_device_id": parent_id, "gpu_device_ids": sorted(child_ids),
            "expected_gpu_count": int(monitor.get("expected_gpu_count") or 0),
            "gpu_index": device.get("gpu_index"), "gpu_uuid": stripped_text(device.get("gpu_uuid")),
            "model": stripped_text(device.get("model")), "observed_at": observed_at,
            **dynamic,
        }
        if device_kind == "gpu_server":
            summary_parts = ["GPU server", stripped_text(device.get("hostname")), stripped_text(network.get("primary_ip"))]
        elif device_kind == "gpu":
            summary_parts = ["GPU", stripped_text(device.get("model"))]
        else:
            summary_parts = [stripped_text(device.get("description")), stripped_text(device.get("model")),
                             stripped_text(device.get("hostname")), stripped_text(network.get("primary_ip"))]
        return resource_record(
            resource_id=device_id, resource_type="device",
            title=stripped_text(device.get("name")) or device_id,
            summary=" · ".join(value for value in summary_parts if value),
            adapter=self.name, modified_at=observed_at or device.get("updated_at"),
            facets=["device", device_kind], attributes=attributes,
            search_terms=[device_kind, device.get("hostname"), network.get("primary_ip"),
                          *(network.get("mac_addresses") or []), device.get("gpu_uuid"),
                          device.get("model"), parent_id],
        )

    def scan_changes(self, paths, previous_records, previous_state) -> ChangeSet:
        del previous_state
        registry = get_registry_store()
        changed_ids = {
            change[1] for change in map(parse_registry_change_key, paths)
            if change is not None and change[0] == device_identity_backend.DEVICE_COLLECTION
        }
        if not changed_ids:
            return ChangeSet(self.name)
        try:
            observations = device_identity_backend.load_device_observations(self.roots[0])
        except Exception:
            observations = {}
        children: dict[str, set[str]] = {}
        affected_parents = {
            stripped_text((record.get("attributes") or {}).get("parent_device_id"))
            for record in previous_records
            if stripped_text(record.get("resource_id")) in changed_ids
        }
        for record in previous_records:
            parent_id = stripped_text((record.get("attributes") or {}).get("parent_device_id"))
            if parent_id and stripped_text(record.get("resource_id")) not in changed_ids:
                children.setdefault(parent_id, set()).add(stripped_text(record.get("resource_id")))
        upserts = []
        deletes = []
        for resource_id in sorted(changed_ids):
            raw = registry.get(device_identity_backend.DEVICE_COLLECTION, resource_id)
            if raw is None:
                deletes.append(resource_id)
                continue
            device = device_identity_backend._normalize_record(raw, resource_id)
            parent_id = stripped_text(device.get("parent_device_id"))
            if parent_id:
                affected_parents.add(parent_id)
                children.setdefault(parent_id, set()).add(resource_id)
            upserts.append(self.project_record("device", device,
                observation=observations.get(resource_id), child_ids=children.get(resource_id, ())))
        for parent_id in sorted(affected_parents - changed_ids - {""}):
            raw = registry.get(device_identity_backend.DEVICE_COLLECTION, parent_id)
            if raw is not None:
                parent = device_identity_backend._normalize_record(raw, parent_id)
                upserts.append(self.project_record("device", parent,
                    observation=observations.get(parent_id), child_ids=children.get(parent_id, ())))
        return ChangeSet(self.name, tuple(upserts), tuple(deletes))

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        devices, resource_errors = device_identity_backend.scan_device_records()
        try:
            observations = device_identity_backend.load_device_observations(
                self.roots[0]
            )
        except Exception as exc:
            observations = {}
            resource_errors.append(
                {
                    "path": str(self.roots[0]),
                    "error": str(exc)[:1000],
                }
            )
        child_ids: dict[str, list[str]] = {}
        for device in devices:
            parent_id = stripped_text(device.get("parent_device_id"))
            if parent_id:
                child_ids.setdefault(parent_id, []).append(stripped_text(device.get("device_id")))

        records: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for device in devices:
            device_id = stripped_text(device.get("device_id"))
            try:
                device_kind = stripped_text(device.get("device_kind"))
                counts[device_kind] = counts.get(device_kind, 0) + 1
                records.append(
                    self.project_record(
                        "device", device, observation=observations.get(device_id),
                        child_ids=child_ids.get(device_id) or (),
                    )
                )
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": device_id,
                        "error": str(exc)[:1000],
                    }
                )
        return records, {
            "counts": counts,
            "observation_count": len(observations),
            "status_path": str(self.roots[0]),
            "resource_errors": resource_errors,
        }


class SavedSearchAdapter(_PathsAdapter):
    name = "saved_search"
    resource_types = frozenset({"search"})
    def fingerprint(self) -> str:
        return _CURRENT_PROJECTION_CODE_FINGERPRINT + ':' + semantic_search_store.fingerprint()

    def scan(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records = []
        source_records, resource_errors = (
            search_identity_backend.scan_search_resources()
        )
        for item in source_records:
            search_id = stripped_text(item.get("id"))
            surface = stripped_text(item.get("surface"))
            project_id = stripped_text(item.get("project"))
            try:
                records.append(resource_record(
                    resource_id=search_id,
                    resource_type="search",
                    title=item.get("query") or search_id,
                    summary=f"{surface.title()} semantic search · {int(item.get('result_count') or 0)} results",
                    adapter=self.name,
                    modified_at=_timestamp_iso(item.get("updated_at") or item.get("created_at")),
                    facets=["search", surface],
                    attributes={
                        "surface": surface,
                        "resource_scope": stripped_text(item.get("resource_scope")),
                        "project_ids": [project_id] if project_id else [],
                        "search_kind": stripped_text(item.get("search_kind")),
                        "status": stripped_text(item.get("status")),
                        "result_count": int(item.get("result_count") or 0),
                        "created_by": stripped_text(item.get("created_by")),
                    },
                    search_terms=[surface, item.get("search_kind"), item.get("status")],
                ))
            except Exception as exc:
                resource_errors.append(
                    {
                        "resource_id": search_id,
                        "error": str(exc)[:1000],
                    }
                )
        return records, {
            "search_count": len(records),
            "resource_errors": resource_errors,
        }


class WorkbenchAdapter(_PathsAdapter):
    name = "workbench"
    resource_types = frozenset({"workbench"})

    def __init__(self) -> None:
        self.roots = ()

    def source_paths(self) -> list[Path]:
        return []

    def fingerprint(self) -> str:
        store = portal_database(schema="workbenches")
        digest = hashlib.sha256(_CURRENT_PROJECTION_CODE_FINGERPRINT.encode("ascii"))
        with store.read_transaction() as connection:
            if connection.execute("SELECT to_regclass('apps')").fetchone()[0] is None:
                return digest.hexdigest()
            for row in connection.execute(
                "SELECT id, revision, published, updated_at FROM apps ORDER BY id"
            ):
                digest.update(json.dumps(
                    (row["id"], row["revision"], row["published"], iso_time(row["updated_at"])),
                    ensure_ascii=False, separators=(",", ":"),
                ).encode())
                digest.update(b"\n")
        return digest.hexdigest()

    def project_record(self, resource_type: str, app: dict[str, Any]) -> dict[str, Any]:
        return resource_record(
            resource_id=app.get("resource_id") or app["workbench_id"],
            resource_type="workbench", title=app["title"],
            summary=app.get("description", ""), adapter=self.name,
            modified_at=app.get("updated_at", ""), facets=["workbench"],
            attributes={
                "created_by": app["created_by"], "project_ids": app.get("project_ids", []),
                "bindings": app["bindings"],
            },
        )

    def scan(self):
        store = portal_database(schema="workbenches")
        with store.read() as connection:
            if connection.execute("SELECT to_regclass('apps')").fetchone()[0] is None:
                return [], {"workbench_count": 0}
            from .workbench_projections.tables import manifest
            apps = [manifest(connection, row) for row in connection.execute("SELECT * FROM apps").fetchall()]
        records = [self.project_record("workbench", app) for app in apps]
        return records, {"workbench_count": len(records)}


DEFAULT_ADAPTERS = (
    WorkbenchAdapter,
    ProjectKnowledgeAdapter,
    SkillPackageAdapter,
    ProjectRegistryAdapter,
    CollaborationRegistryAdapter,
    ResourceRegistryAdapter,
    DeviceRegistryAdapter,
    SavedSearchAdapter,
)


def _publish_projection_change(adapter: str, result: dict[str, Any]) -> None:
    if str(os.getenv("PORTAL_RESOURCE_SERVICE_PROCESS", "") or "").strip() != "1":
        return
    changed_ids = sorted(
        {
            stripped_text(resource_id)
            for resource_id in [
                *(result.get("upserted_ids") or []),
                *(result.get("deleted_ids") or []),
            ]
            if stripped_text(resource_id)
        }
    )
    snapshot_required = len(changed_ids) > 128
    get_notification_publisher().publish(
        resource_projection_subject(adapter),
        {
            "kind": "projection_updated",
            "adapter": adapter,
            "upserted": int(result.get("upserted") or 0),
            "deleted": int(result.get("deleted") or 0),
            "resource_ids": [] if snapshot_required else changed_ids,
            "snapshot_required": snapshot_required,
        },
    )


class ResourceIndexService(ResourceIndexRuntime):
    def __init__(
        self,
        *,
        store: IndexStore | None = None,
        adapters: Iterable[ResourceAdapter] | None = None,
        start_worker: bool = True,
        portal_access: PortalAccessStore | None = None,
    ) -> None:
        store_instance = store or IndexStore(
            portal_access=portal_access,
        )
        selected_adapters = (
            list(adapters)
            if adapters is not None
            else [adapter() for adapter in (
                *DEFAULT_ADAPTERS[:3],
                *(adapter for _feature, adapters in portal_features.contributions("resource.index_adapters")
                  for adapter in adapters),
                *DEFAULT_ADAPTERS[3:],
            )]
        )
        if adapters is None:
            store_instance.discard_inactive_adapters(
                adapter.name for adapter in selected_adapters
            )
        super().__init__(
            store=store_instance,
            adapters=selected_adapters,
            adapter_layer_factory=AdapterLayer,
            start_worker=start_worker,
            on_projection_changed=_publish_projection_change,
        )


_SERVICE_LOCK = threading.Lock()
_SERVICE: ResourceIndexService | None = None


def get_service() -> ResourceIndexService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = ResourceIndexService(
                    portal_access=get_portal_access_store(),
                )
    return _SERVICE

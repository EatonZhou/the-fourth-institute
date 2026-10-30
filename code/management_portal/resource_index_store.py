"""The resource index store: records held in memory and persisted per adapter, queried with current facts."""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from management_common.json_columns import json_text, json_value
from management_common.portal_database import portal_database
from management_portal.portal_access_control import PortalAccessStore
from management_portal.resources import timing
from management_common.format_tags import schema_matches
from management_common.text_values import stripped_text
from management_portal.resources.contracts import ResourceQuery
from management_portal.resources.index import ResourceRecordsProjector
from management_portal.resources.query import (
    filter_query_candidates,
    prefilter_query_candidates,
    query_records,
    readable_flags,
    score_id_title,
)
from management_portal.resources.portal import portal_url_for_resource
from management_portal.resources.schema import RESOURCE_INDEX_SCHEMA
from management_portal.resources.config import resource_database_schema
from management_portal.resources.timing import ReportingLock

from management_portal.resource_index_records import _now_iso, _json_value, _validate_record, ChangeSet


SCHEMA = RESOURCE_INDEX_SCHEMA
RESOURCE_INDEX_STORE_SCHEMA = "tfi.resource-index-store.v1"


# A query slower than this reports its step timings (see resources/timing.py).
SLOW_QUERY_SECONDS = 0.3


def _resolved_source_path(raw_path: str) -> str:
    return str(Path(raw_path).expanduser().resolve(strict=False))


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
        resource_errors = [
            dict(item)
            for item in changes.source_state.get("resource_errors") or []
            if isinstance(item, dict)
        ]
        validated, protected_ids = self._validated_upserts(changes, adapter, resource_errors)
        delete_ids = self._conflict_free_deletes(changes, set(validated) | protected_ids, resource_errors)

        now = _now_iso()
        with self._lock:
            if scan_started_generation is not None:
                self._drop_newer_direct_changes(adapter, scan_started_generation, validated, delete_ids)
            current_ids = set(self._adapter_ids.get(adapter) or set())
            self._drop_foreign_resources(adapter, validated, delete_ids, resource_errors)
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
                self._replace_records(validated, effective_deletes, now)
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

    def _validated_upserts(
        self, changes: ChangeSet, adapter: str, resource_errors: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """The ChangeSet's valid records of enabled types, and the IDs no delete may touch.

        An invalid or duplicated record becomes a resource error; its ID stays
        protected so that the same ChangeSet cannot delete it either.
        """
        validated: dict[str, dict[str, Any]] = {}
        enabled_types = self.resource_types.enabled_types()
        protected_ids: set[str] = set()
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
        return validated, protected_ids

    @staticmethod
    def _conflict_free_deletes(
        changes: ChangeSet, upserted_ids: set[str], resource_errors: list[dict[str, Any]],
    ) -> set[str]:
        """The ChangeSet's delete IDs, less those it also upserts (each a resource error)."""
        delete_ids = {stripped_text(value) for value in changes.deletes if stripped_text(value)}
        conflicts = delete_ids & upserted_ids
        for resource_id in sorted(conflicts):
            delete_ids.discard(resource_id)
            resource_errors.append(
                {
                    "resource_id": resource_id,
                    "error": "ChangeSet cannot upsert and delete the same resource",
                }
            )
        return delete_ids

    def _drop_newer_direct_changes(
        self, adapter: str, scan_started_generation: int,
        validated: dict[str, dict[str, Any]], delete_ids: set[str],
    ) -> None:
        """Leave out what a direct change applied after the scan started (the lock is held)."""
        newer = {
            resource_id for resource_id, revision in
            self._direct_revisions.get(adapter, {}).items()
            if revision > scan_started_generation
        }
        for resource_id in newer:
            validated.pop(resource_id, None)
        delete_ids.difference_update(newer)

    def _drop_foreign_resources(
        self, adapter: str, validated: dict[str, dict[str, Any]], delete_ids: set[str],
        resource_errors: list[dict[str, Any]],
    ) -> None:
        """Leave out upserts and deletes of records another adapter owns (the lock is held)."""
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

    def _replace_records(
        self, validated: dict[str, dict[str, Any]], effective_deletes: set[str], now: str,
    ) -> None:
        """Replace and delete records and their type entries (the lock is held)."""
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

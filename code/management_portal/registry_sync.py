"""Keep a resource registry collection in step with what a scan finds on disk.

A scan reports groups: each group lists the on-disk locations (Git
checkouts, manuscript roots) that make up one logical resource.  A
``RegistrySync`` declaration names the match keys a stored record is known
by and the keys one scanned location looks up.  ``sync`` gives each group
the ID of the stored record it shares a key with (or a new ID), lets the
caller merge the group into that record, and writes the records that
changed.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Hashable, Iterable

from management_portal.resources import get_resource_service
from management_portal.resources.registry_store import announce_registry_changes, get_registry_store


Record = dict[str, Any]


def resolved_path(value: object) -> str:
    return str(Path(str(value or "").strip()).resolve(strict=False))


def merge_locations(stored: Iterable[object], current: Iterable[Record]) -> list[Record]:
    """The scanned locations over the stored ones, sorted by path.

    A location is identified by its resolved ``path``; a scanned location
    keeps the stored fields it does not set itself.  A stored location the
    scan no longer finds stays in the record with ``present`` refreshed, so
    a moved or unmounted directory does not lose its resource ID.
    """
    previous = {
        resolved_path(item.get("path")): dict(item)
        for item in stored
        if isinstance(item, dict) and item.get("path")
    }
    merged: dict[str, Record] = {}
    for item in current:
        path = resolved_path(item["path"])
        merged[path] = {**previous.get(path, {}), **item, "path": path}
    for path, item in previous.items():
        if path not in merged:
            merged[path] = {**item, "present": Path(path).is_dir()}
    return sorted(merged.values(), key=lambda item: str(item.get("path") or "").strip().casefold())


@dataclass(frozen=True)
class RegistrySync:
    resource_type: str
    collection: str
    # What one scanned location is called in error messages.
    location: str
    record_keys: Callable[[Record], Iterable[Hashable]]
    location_keys: Callable[[Record], Iterable[Hashable]]

    def validated(self, document_id: str, record: object) -> Record:
        if not isinstance(record, dict):
            raise ValueError(f"{self.resource_type} registry document must be an object: {document_id}")
        record_id = str(record.get("id") or "").strip()
        if not get_resource_service().is_valid_id(record_id, self.resource_type):
            raise ValueError(f"invalid {self.resource_type} id in registry: {record_id!r}")
        if record_id != document_id:
            raise ValueError(f"registry id mismatch: {document_id}")
        return record

    def records(self, source: Any = None) -> dict[str, Record]:
        """Every stored record by ID, read from ``source`` (a registry writer) or the store."""
        documents = (get_registry_store() if source is None else source).documents(self.collection)
        return {key: self.validated(key, value) for key, value in documents.items()}

    def sync(
        self,
        groups: list[list[Record]],
        *,
        merge: Callable[[Record | None, str, list[Record]], Record],
        apply: bool,
        forced_id: str = "",
    ) -> dict[str, Any]:
        """Match, merge and (with ``apply``) write every group.

        ``merge(stored, record_id, group)`` returns the record for a group;
        ``stored`` is ``None`` for a new record.  ``forced_id`` attaches a
        single group to an existing record whatever its keys say, unless the
        group already belongs to another record.
        """
        kind = self.resource_type
        service = get_resource_service()
        if forced_id and not service.is_valid_id(forced_id, kind):
            raise ValueError(f"invalid {kind} id: {forced_id}")
        if forced_id and len(groups) != 1:
            raise ValueError(f"an explicit {kind} id can only register one {self.location}")
        changes: dict[str, str] = {}
        store = get_registry_store()
        with (store.writer(self.collection) if apply else nullcontext()) as writer:
            stored = self.records(writer)
            if forced_id and forced_id not in stored:
                raise KeyError(f"{kind} id is not registered: {forced_id}")
            index: dict[Hashable, set[str]] = {}
            for record_id, record in stored.items():
                for key in self.record_keys(record):
                    index.setdefault(key, set()).add(record_id)
            used_ids = set(stored)
            created: list[Record] = []
            updated: list[Record] = []
            unchanged: list[str] = []
            for group in groups:
                matches = {
                    record_id
                    for item in group
                    for key in self.location_keys(item)
                    for record_id in index.get(key, ())
                }
                if len(matches) > 1:
                    paths = ", ".join(item["path"] for item in group)
                    raise ValueError(f"{self.location} group matches multiple {kind} ids {sorted(matches)}: {paths}")
                if forced_id and matches and matches != {forced_id}:
                    raise ValueError(
                        f"{self.location} already belongs to {sorted(matches)}; cannot attach it to {forced_id}"
                    )
                record_id = forced_id or next(iter(matches), "") or service.new_id(kind, used_ids)
                used_ids.add(record_id)
                existing = stored.get(record_id)
                merged = merge(existing, record_id, group)
                if existing is None:
                    created.append(merged)
                    change = "created"
                elif _without_timestamp(existing) != _without_timestamp(merged):
                    updated.append(merged)
                    change = "updated"
                else:
                    unchanged.append(record_id)
                    continue
                # A later group of the same scan merges into this result.
                stored[record_id] = merged
                if writer is not None:
                    writer.put(self.collection, record_id, merged)
                    changes.setdefault(record_id, change)
        announce_registry_changes(self.collection, changes, resource_type=kind)
        return {
            "applied": apply,
            f"logical_{kind}_count": len(groups),
            "created_count": len(created),
            "updated_count": len(updated),
            "unchanged_count": len(unchanged),
            "created": created,
            "updated": updated,
            "unchanged": sorted(unchanged),
        }


def _without_timestamp(record: Record) -> Record:
    return {key: value for key, value in record.items() if key != "updated_at"}

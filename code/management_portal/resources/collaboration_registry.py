from __future__ import annotations

import copy
import json
from contextlib import nullcontext
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from management_common.format_tags import schema_matches

from .registry_store import RegistryStore, get_registry_store, registry_change_key
from .schema import normalize_resource_attributes
from . import timing
from .portal import portal_url_for_resource
from management_common.resource_identity import (
    ResourceTypeRegistry,
    get_resource_type_registry,
)
from management_common.text_values import stripped_text


COLLABORATION_DESCRIPTOR_SCHEMA = "tfi.collaboration-descriptor.v2"
RESOURCE_TYPES = frozenset({"resident", "lineage", "workspace", "family"})
STATUSES = frozenset({"active", "creating", "running", "deleted"})


def descriptor_attributes(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Project current domain fields without retaining removed index fields."""
    return {
        **dict(descriptor.get("attributes") or {}),
        "status": descriptor.get("status", ""),
        "project_ids": list(descriptor.get("project_ids") or []),
        "relations": list(descriptor.get("relations") or []),
        "updated_at": descriptor.get("updated_at", ""),
        "deleted_at": descriptor.get("deleted_at", ""),
    }


def descriptor_resource_record(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """One projection for direct identity reads and the discovery index."""
    resource_id = stripped_text(descriptor.get("resource_id"))
    resource_type = get_resource_type_registry().parse(resource_id).resource_type
    title = stripped_text(descriptor.get("title")) or resource_id
    summary = stripped_text(descriptor.get("summary"))
    attributes = normalize_resource_attributes(descriptor_attributes(descriptor))
    terms = [
        *attributes.get("project_ids", []),
        *(descriptor.get("attributes") or {}).values(),
        *(relation.get("target_id") for relation in attributes.get("relations", [])),
    ]
    record = {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "title": title,
        "summary": summary,
        "source": {
            "adapter": "collaboration", "path": "",
            "modified_at": stripped_text(descriptor.get("updated_at") or descriptor.get("created_at")),
        },
        "facets": [resource_type, "collaboration"],
        "attributes": attributes,
        "search_terms": _dedupe([resource_id, title, *(term for term in terms if stripped_text(term) != summary)]),
        "url": portal_url_for_resource(resource_id, resource_type, record={"attributes": attributes}),
    }
    if resource_type == "workspace":
        record["version"] = stripped_text(descriptor.get("updated_at"))
    return record


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dedupe(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = stripped_text(value)
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


class CollaborationDescriptorStore:
    """ResourceService-owned lightweight descriptors for Lineage objects.

    Each resource type is one registry collection; the cache of a type is
    brought to the registry's revision before every read.
    """

    def __init__(
        self,
        *,
        registry: RegistryStore | None = None,
        identity: ResourceTypeRegistry | None = None,
    ) -> None:
        self._registry = registry or get_registry_store()
        self._identity = identity or get_resource_type_registry()
        self._cache_lock = threading.RLock()
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._revisions: dict[str, int] = {}
        # Discovery reads only requested descriptors. Per-document revisions
        # preserve hot entries when another Resident's descriptor changes.
        self._attribute_cache: dict[str, dict[str, tuple[int, str]]] = {}

    def _resource_type(self, resource_id: str) -> str:
        resource_type = self._identity.parse(stripped_text(resource_id)).resource_type
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(
                f"unsupported collaboration resource type: {resource_type}"
            )
        return resource_type

    def change_key(self, resource_id: str) -> str:
        """The key a registry change of this descriptor is announced under."""
        clean_id = stripped_text(resource_id)
        return registry_change_key(self._resource_type(clean_id), clean_id)

    def _load_type_locked(self, resource_type: str) -> None:
        revision, documents = self._registry.snapshot(resource_type)
        self._records[resource_type] = {
            key: self.normalize(value, expected_id=key) for key, value in documents.items()
        }
        self._revisions[resource_type] = revision

    def _refresh_type_locked(self, resource_type: str) -> None:
        cached = self._revisions.get(resource_type, -1)
        if cached < 0:
            self._load_type_locked(resource_type)
            return
        current = self._registry.revision(resource_type)
        if current == cached:
            return
        if current < cached:
            self._load_type_locked(resource_type)
            return
        revision, changed, count = self._registry.changes_since(resource_type, cached)
        records = self._records.setdefault(resource_type, {})
        for key, value in changed.items():
            records[key] = self.normalize(value, expected_id=key)
        self._revisions[resource_type] = revision
        if len(records) != count:
            self._load_type_locked(resource_type)

    def normalize(
        self,
        raw: Mapping[str, Any],
        *,
        expected_id: str = "",
    ) -> dict[str, Any]:
        unknown = set(raw) - {
            "schema",
            "resource_id",
            "title",
            "summary",
            "status",
            "project_ids",
            "relations",
            "attributes",
            "created_at",
            "updated_at",
            "deleted_at",
        }
        if unknown:
            raise ValueError(
                "unsupported collaboration descriptor fields: "
                + ", ".join(sorted(unknown))
            )
        if not schema_matches(raw.get("schema"), expected="tfi.collaboration-descriptor.v2"):
            raise ValueError("unsupported collaboration descriptor schema")
        resource_id = self._identity.parse(stripped_text(raw.get("resource_id"))).resource_id
        if expected_id and resource_id != expected_id:
            raise ValueError(
                f"descriptor resource_id {resource_id!r} does not match {expected_id!r}"
            )
        self._resource_type(resource_id)
        status = stripped_text(raw.get("status") or "active").casefold()
        if status not in STATUSES:
            raise ValueError(f"invalid collaboration descriptor status: {status!r}")
        attributes = raw.get("attributes") or {}
        if not isinstance(attributes, Mapping):
            raise ValueError("collaboration descriptor attributes must be an object")
        forbidden_attributes = set(attributes) & {
            "resource_type",
            "portal_url",
            "owner_service",
            "capabilities",
            "visibility",
            "resource_capabilities",
        }
        if forbidden_attributes:
            raise ValueError(
                "Derived fields cannot be stored in a collaboration descriptor: "
                + ", ".join(sorted(forbidden_attributes))
            )
        relations: list[dict[str, str]] = []
        seen_relations: set[tuple[str, str]] = set()
        for item in raw.get("relations") or []:
            if not isinstance(item, Mapping) or set(item) != {"kind", "target_id"}:
                raise ValueError("collaboration descriptor relation is invalid")
            kind = stripped_text(item.get("kind"))
            target_id = self._identity.parse(stripped_text(item.get("target_id"))).resource_id
            key = (kind, target_id)
            if not kind or key in seen_relations:
                continue
            seen_relations.add(key)
            relations.append({"kind": kind, "target_id": target_id})
        return {
            "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
            "resource_id": resource_id,
            "title": stripped_text(raw.get("title")) or resource_id,
            "summary": stripped_text(raw.get("summary")),
            "status": status,
            "project_ids": _dedupe(raw.get("project_ids") or []),
            "relations": relations,
            "attributes": copy.deepcopy(dict(attributes)),
            "created_at": stripped_text(raw.get("created_at")),
            "updated_at": stripped_text(raw.get("updated_at")) or _now_iso(),
            "deleted_at": stripped_text(raw.get("deleted_at")),
        }

    def get(self, resource_id: str) -> dict[str, Any]:
        clean_id = stripped_text(resource_id)
        resource_type = self._resource_type(clean_id)
        if resource_type == "workspace":
            from .workspace_store import get_workspace_store
            stored = get_workspace_store().descriptors([clean_id]).get(clean_id)
        else:
            stored = self._registry.get(resource_type, clean_id)
        if stored is None:
            raise KeyError(resource_id)
        return self.normalize(stored, expected_id=clean_id)

    def attributes_for_records(
        self,
        records: Iterable[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return current attributes for type-validated resource index rows."""
        grouped: dict[str, list[str]] = {}
        for record in records:
            resource_type = stripped_text(record.get("resource_type"))
            clean_id = stripped_text(record.get("resource_id"))
            if resource_type not in RESOURCE_TYPES or not clean_id.startswith(resource_type + "_"):
                continue
            grouped.setdefault(resource_type, []).append(clean_id)
        projected: dict[str, dict[str, Any]] = {}
        for resource_type, identifiers in grouped.items():
            mark = time.perf_counter()
            if resource_type == "workspace":
                from .workspace_store import get_workspace_store
                documents = get_workspace_store().descriptors(identifiers)
                mark = timing.lap("descriptor_workspace_fetch", mark)
                for resource_id, stored in documents.items():
                    projected[resource_id] = descriptor_attributes(self.normalize(stored, expected_id=resource_id))
                timing.lap("descriptor_workspace_normalize", mark)
                continue

            revisions = self._registry.document_revisions(resource_type, identifiers)
            mark = timing.lap("descriptor_revisions", mark)
            with self._cache_lock:
                cache = self._attribute_cache.setdefault(resource_type, {})
                for resource_id in identifiers:
                    if resource_id not in revisions:
                        cache.pop(resource_id, None)
                available = {
                    resource_id: json.loads(cache[resource_id][1])
                    for resource_id in identifiers
                    if resource_id in revisions
                    and resource_id in cache
                    and cache[resource_id][0] == revisions[resource_id]
                }
                missing = [resource_id for resource_id in revisions if resource_id not in available]
            mark = timing.lap("descriptor_cache_lookup", mark)
            if missing:
                documents = self._registry.get_many(resource_type, missing)
                mark = timing.lap("descriptor_fetch", mark)
                fetched = {
                    resource_id: descriptor_attributes(self.normalize(stored, expected_id=resource_id))
                    for resource_id, stored in documents.items()
                }
                mark = timing.lap("descriptor_normalize", mark)
                with self._cache_lock:
                    for resource_id, attributes in fetched.items():
                        revision = revisions[resource_id]
                        previous = cache.get(resource_id)
                        if previous is None or previous[0] <= revision:
                            cache[resource_id] = (revision, json.dumps(attributes, ensure_ascii=False))
                available.update(fetched)
            projected.update(available)
            timing.lap("descriptor_copy", mark)
        return projected

    def put_with_change(
        self,
        raw: Mapping[str, Any],
        *, writer=None,
    ) -> tuple[dict[str, Any], bool]:
        descriptor = self.normalize(raw)
        resource_id = descriptor["resource_id"]
        resource_type = self._resource_type(resource_id)
        if resource_type == "workspace":
            raise ValueError("Workspace mutations must use WorkspaceStore")
        with (self._registry.writer(resource_type) if writer is None else nullcontext(writer)) as writer:
            stored = writer.get(resource_type, resource_id)
            existing = None if stored is None else self.normalize(stored, expected_id=resource_id)
            if existing is not None and not stripped_text(raw.get("updated_at")):
                descriptor["updated_at"] = existing["updated_at"]
            if existing == descriptor:
                return copy.deepcopy(existing), False
            writer.put(resource_type, resource_id, descriptor)
        with self._cache_lock:
            records = self._records.get(resource_type)
            if records is not None:
                advanced = writer.advances(resource_type, self._revisions.get(resource_type, -1))
                if advanced is None:
                    self._revisions[resource_type] = -1
                else:
                    records[resource_id] = copy.deepcopy(descriptor)
                    self._revisions[resource_type] = advanced
        return copy.deepcopy(descriptor), True

    def projection_scope(self, *, connection=None):
        """Serialize a bundle's descriptor lifecycle."""
        from .domain_projection_evidence import EVIDENCE_COLLECTION, SOURCE_COLLECTION
        return self._registry.writer(*(RESOURCE_TYPES - {"workspace"}), EVIDENCE_COLLECTION, SOURCE_COLLECTION,
                                     "domain_live_sources", connection=connection)

    def tombstone(self, incoming: Mapping[str, Any], writer, *, source: Mapping[str, Any],
                  trusted_domain: bool = False) -> dict[str, Any]:
        """Merge deletion only, under the descriptor writer's lifecycle lock."""
        candidate = self.normalize(incoming)
        if candidate["status"] != "deleted" or not candidate["deleted_at"]:
            raise ValueError("Domain tombstone requires deleted status and deleted_at")
        resource_id = candidate["resource_id"]
        resource_type = self._resource_type(resource_id)
        def instant(value):
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Domain projection time must include a timezone")
            return parsed
        deletion_time = instant(candidate["deleted_at"])
        stored = writer.get(resource_type, resource_id)
        if stored is None:
            return candidate
        current = self.normalize(stored, expected_id=resource_id)
        current_time = max(instant(current["updated_at"]),
                           instant(current["deleted_at"]) if current["deleted_at"] else instant(current["updated_at"]))
        if current["status"] == "deleted" and instant(current["deleted_at"]) == deletion_time:
            from .domain_projection_evidence import SOURCE_COLLECTION, matches_tombstone_source
            # The merged descriptor may retain independent edits, so compare
            # the original accepted payload rather than its resulting fields.
            accepted = writer.get(SOURCE_COLLECTION, resource_id)
            if accepted != source and not (matches_tombstone_source(accepted, candidate)
                                           and matches_tombstone_source(source, candidate)):
                from .domain_projection_evidence import EVIDENCE_COLLECTION, historical_tombstone_retry
                receipt = writer.get(EVIDENCE_COLLECTION, resource_id) or {}
                # Receipts written before 2026-10-19 name the domain lineage-service.
                historical_retry = historical_tombstone_retry(accepted, receipt, candidate)
                if not historical_retry:
                    raise ValueError(f"Stale domain tombstone: {resource_id}")
            return current
        lifecycle_fields = {"status", "updated_at", "deleted_at"}
        same_source = ({k: v for k, v in candidate.items() if k not in lifecycle_fields}
                       == {k: v for k, v in current.items() if k not in lifecycle_fields})
        # A deletion can retire a Workspace binding in the same second as the
        # last live projection. Accept that domain event only while the public
        # descriptor still exactly matches the last accepted live source.
        from .domain_projection_evidence import same_descriptor
        unchanged_live_source = (trusted_domain and current["status"] != "deleted"
                                 and same_descriptor(writer.get("domain_live_sources", resource_id), current))
        if current_time > deletion_time or (
                current_time == deletion_time and current["status"] != "deleted"
                and not (same_source or unchanged_live_source)):
            raise ValueError(f"Stale domain tombstone: {resource_id}")
        # Deletion cannot overwrite a title, attributes or relationships edited
        # independently since the domain snapshot was prepared.
        return {**current, "status": "deleted", "deleted_at": candidate["deleted_at"],
                "updated_at": max(current["updated_at"], candidate["deleted_at"], key=instant)}

    def put(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        descriptor, _changed = self.put_with_change(raw)
        return descriptor

    def list(self, resource_type: str) -> list[dict[str, Any]]:
        clean_type = stripped_text(resource_type)
        if clean_type not in RESOURCE_TYPES:
            raise ValueError(
                f"unsupported collaboration resource type: {clean_type}"
            )
        if clean_type == "workspace":
            from .workspace_store import get_workspace_store
            return list(get_workspace_store().descriptors().values())
        with self._cache_lock:
            self._refresh_type_locked(clean_type)
            records = self._records.get(clean_type, {})
            return [copy.deepcopy(records[resource_id]) for resource_id in sorted(records)]

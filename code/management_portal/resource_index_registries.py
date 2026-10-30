"""Resource index adapters for registry and database records: Projects, collaboration
descriptors, the resource registry, devices, saved searches and Workbenches."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from management_common.portal_database import portal_database
from management_common.database_time import iso_time
from management_common.text_values import stripped_text
from management_portal import project_registry, semantic_search_store, portal_features
from management_portal.resources.providers import project as project_provider
from management_portal.resources.schema import resource_relation
from management_portal.resources.registry_store import get_registry_store, parse_registry_change_key
from management_portal.resources.collaboration_registry import (
    descriptor_resource_record,
    CollaborationDescriptorStore,
)

from . import (
    device_identity_backend,
    resource_identity_backend,
    search_identity_backend,
)

from management_portal.resource_index_adapters import (
    _CURRENT_PROJECTION_CODE_FINGERPRINT, _adapter_projection_code_fingerprint, _PathsAdapter,
)
from management_portal.resource_index_records import (
    RESOURCE_IDENTITY, _timestamp_iso, _json_value, resource_record, ChangeSet,
)


LOGGER = logging.getLogger(__name__)


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
            # A full scan records this in its resource errors; a registry change
            # projects the devices without their observations and says so here.
            LOGGER.warning("device observations unreadable at %s; projecting registry fields only",
                           self.roots[0], exc_info=True)
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

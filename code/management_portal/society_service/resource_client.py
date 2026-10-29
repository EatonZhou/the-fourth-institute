from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable, Mapping

from management_portal.resources import get_resource_service
from management_common.site_registry import rewrite_shared_path
from management_common.text_values import stripped_text

from . import identity_contract


COLLABORATION_DESCRIPTOR_SCHEMA = "tfi.collaboration-descriptor.v2"


def resolve(address: str, *, actor: str = "") -> dict[str, Any]:
    return get_resource_service().resolve(address, actor=stripped_text(actor))


def get(resource_id: str, *, actor: str = "") -> dict[str, Any]:
    return get_resource_service().get(resource_id, actor=stripped_text(actor))


def portal_url_for_address(
    address: str,
    resource_type: str = "",
) -> str:
    return get_resource_service().portal_url_for_address(
        stripped_text(address),
        stripped_text(resource_type),
    )


def _query_all(resource_type: str, *, actor: str = "") -> list[dict[str, Any]]:
    service = get_resource_service()
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = service.query(
            actor=stripped_text(actor),
            resource_types=[resource_type],
            include_attributes=True,
            limit=5000,
            offset=offset,
        )
        resources = [
            dict(item)
            for item in page.get("resources") or []
            if isinstance(item, Mapping)
        ]
        records.extend(resources)
        offset += len(resources)
        if not resources or offset >= max(0, int(page.get("total") or 0)):
            return records


def visible_resident_usage_roots(*, actor: str = "") -> dict[str, dict[str, str]]:
    """Map readable Residents to usage identities, independently readable
    Lineages and the readable Project each such Lineage belongs to."""

    lineages: dict[str, str] = {}
    lineage_projects: dict[str, str] = {}
    for record in _query_all("lineage", actor=actor):
        if not (record.get("capabilities") or {}).get("read"):
            continue
        lineage_id = stripped_text(record.get("resource_id"))
        lineages[lineage_id] = stripped_text(record.get("title"))
        attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
        project_id = next((stripped_text(item) for item in attributes.get("project_ids") or [] if stripped_text(item)), "")
        if project_id:
            lineage_projects[lineage_id] = project_id
    projects = {
        stripped_text(record.get("resource_id")): stripped_text(record.get("title"))
        for record in _query_all("project", actor=actor)
        if (record.get("capabilities") or {}).get("read")
    } if lineage_projects else {}
    visible: dict[str, dict[str, object]] = {}
    for record in _query_all("resident", actor=actor):
        resident_id = stripped_text(record.get("resource_id"))
        capabilities = (
            record.get("capabilities")
            if isinstance(record.get("capabilities"), Mapping)
            else {}
        )
        if not resident_id or not bool(capabilities.get("read")):
            continue
        attributes = (
            record.get("attributes")
            if isinstance(record.get("attributes"), Mapping)
            else {}
        )
        visible[resident_id] = {
            "resident_id": resident_id,
            "title": stripped_text(record.get("title")) or resident_id,
            "kind": stripped_text(attributes.get("kind")),
        }

    # Resource discovery determines readability; the current domain record owns
    # membership. Index relations can retain the source Lineage after a Move.
    from . import resident_catalog
    current = {record["resident_id"]: record
               for record in resident_catalog.get_catalog().get_usage_memberships(visible)}
    roots: dict[str, dict[str, str]] = {}
    for resident_id, record in visible.items():
        canonical = current.get(resident_id)
        if canonical is None:
            continue  # Deleted between discovery and the canonical read.
        lineage_id = stripped_text(canonical.get("lineage_id"))
        root = {"resident_id": resident_id, "title": stripped_text(record.get("title")) or resident_id}
        if lineage_id in lineages:
            root.update({"lineage_id": lineage_id, "lineage_title": lineages[lineage_id] or lineage_id})
            # Usage reaches a Project only through the Resident's current,
            # readable Lineage, and only a readable Project is named.
            project_id = lineage_projects.get(lineage_id, "")
            if project_id in projects:
                root.update({"project_id": project_id, "project_title": projects[project_id] or project_id})
        roots[resident_id] = root
    return roots


def project_roots(*, actor: str = "") -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for record in _query_all("project", actor=actor):
        attributes = (
            record.get("attributes")
            if isinstance(record.get("attributes"), Mapping)
            else {}
        )
        project_id = stripped_text(record.get("resource_id"))
        raw_path = stripped_text(attributes.get("path"))
        if not project_id or not raw_path:
            continue
        path = Path(rewrite_shared_path(raw_path)).expanduser().resolve(strict=False)
        roots[project_id] = path
    return roots


def project_root(project_id: str, *, actor: str = "") -> Path | None:
    """Resolve one readable Project without scanning every visible Project."""

    clean_id = stripped_text(project_id)
    if not clean_id:
        return None
    try:
        record = get_resource_service().get(clean_id, actor=stripped_text(actor))
    except (KeyError, FileNotFoundError):
        return None
    if (
        stripped_text(record.get("resource_id")) != clean_id
        or stripped_text(record.get("resource_type")) != "project"
    ):
        return None
    attributes = (
        record.get("attributes")
        if isinstance(record.get("attributes"), Mapping)
        else {}
    )
    raw_path = stripped_text(attributes.get("path"))
    if not raw_path:
        return None
    return Path(rewrite_shared_path(raw_path)).expanduser().resolve(strict=False)


def project_ids_for_path(path: str | Path, *, actor: str = "") -> list[str]:
    candidate = Path(path).expanduser().resolve(strict=False)
    matches: list[tuple[int, str]] = []
    for project_id, root in project_roots(actor=actor).items():
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        matches.append((len(str(root)), project_id))
    return [project_id for _length, project_id in sorted(matches, reverse=True)]


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


def _relation(kind: str, target_id: object) -> dict[str, str] | None:
    target = stripped_text(target_id)
    if not target:
        return None
    identity_contract.validate_id(target)
    return {"kind": kind, "target_id": target}


def _descriptor(
    resource_id: str,
    *,
    title: object,
    summary: object = "",
    status: object = "active",
    project_ids: Iterable[object] = (),
    relations: Iterable[dict[str, str] | None] = (),
    attributes: Mapping[str, Any] | None = None,
    created_at: object = "",
    updated_at: object = "",
    deleted_at: object = "",
) -> dict[str, Any]:
    return {
        "schema": COLLABORATION_DESCRIPTOR_SCHEMA,
        "resource_id": resource_id,
        "title": stripped_text(title) or resource_id,
        "summary": stripped_text(summary),
        "status": stripped_text(status) or "active",
        "project_ids": _dedupe(project_ids),
        "relations": [item for item in relations if item is not None],
        "attributes": copy.deepcopy(dict(attributes or {})),
        "created_at": stripped_text(created_at),
        "updated_at": stripped_text(updated_at),
        "deleted_at": stripped_text(deleted_at),
    }


def workspace_id_for_path(path: str | Path, *, preferred_id: str = "") -> str:
    service = get_resource_service()
    preferred = stripped_text(preferred_id)
    return service.workspace_id_for_path(path, preferred_id=preferred)


def workspace_paths_many(workspace_ids: Iterable[object], *, connection: Any = None) -> dict[str, str]:
    from management_portal.resources.workspace_store import get_workspace_store
    return get_workspace_store().paths(_dedupe(workspace_ids), connection=connection)


def ensure_workspaces(record: Mapping[str, Any], *, connection: Any) -> None:
    """Commit Workspace rows with their owning Resident and usage relationships."""
    from management_portal.resources.workspace_store import get_workspace_store, canonical_path
    from .resident_workspace_storage import normalize_bindings
    store = get_workspace_store()
    own_id = record['owned_workspace_id']
    own = record['owned_workspace']
    expected = canonical_path(Path(record['workspace_path']) / 'residents' / record['resident_id'] / 'workspace')
    if own['workspace_id'] != own_id or own['path'] != expected:
        raise ValueError('Resident owned Workspace identity or location mismatch')
    store.ensure(own_id, expected, connection=connection, move=True)
    normalize_bindings(record['workspace_bindings'])
    for binding in sorted(record['workspace_bindings'], key=lambda item: item['workspace_id']):
        if binding['workspace_id'] == own_id:
            raise ValueError('Owned Workspace cannot also be a usage binding')
        store.ensure(binding['workspace_id'], binding['path'], connection=connection)


def _sync_many(
    projections: Iterable[Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    payload = [
        {
            "descriptor": copy.deepcopy(dict(item.get("descriptor") or {})),
        }
        for item in projections
    ]
    if not payload:
        return []
    result = get_resource_service().sync_collaboration_resources(payload)
    return [
        dict(item)
        for item in result.get("resources") or []
        if isinstance(item, Mapping)
    ]


def sync_resident(
    record: Mapping[str, Any],
    *,
    updated_by: object = "",
) -> dict[str, Any]:
    projection = _resident_resource_projection(
        record,
        updated_by=updated_by,
    )
    return get_resource_service().sync_collaboration_resource(
        projection["descriptor"],
    )


def _resident_resource_projection(
    record: Mapping[str, Any],
    *,
    updated_by: object = "",
) -> dict[str, dict[str, Any]]:
    resident_id = stripped_text(record.get("resident_id"))
    identity_contract.validate_id(resident_id, "resident")
    lineage_id = stripped_text(record.get("lineage_id"))
    if lineage_id or not stripped_text(record.get("deleted_at")):
        identity_contract.validate_id(lineage_id, "lineage")
    source_resident_id = stripped_text(record.get("source_resident_id"))
    workspace_ids = _dedupe(
        [
            record.get("owned_workspace_id"),
            *[
                item.get("workspace_id")
                for item in record.get("workspace_bindings") or []
                if isinstance(item, Mapping)
            ],
        ]
    )
    relations = [
        _relation("lineage", lineage_id),
        _relation("source", source_resident_id)
        if source_resident_id
        else None,
        *[_relation("workspace", workspace_id) for workspace_id in workspace_ids],
    ]
    status = "deleted" if stripped_text(record.get("deleted_at")) else "active"
    descriptor = _descriptor(
        resident_id,
        title=record.get("title"),
        summary=record.get("description"),
        status=status,
        project_ids=[record.get("project_id")],
        relations=relations,
        attributes={
            "principal_hierarchy": dict(record.get("principal_hierarchy") or {}),
            "personal_owner_id": stripped_text(record.get("personal_owner_id")),
            "backend": stripped_text(record.get("backend") or ("" if record.get("deleted_at") else "codex")),
            "created_by": stripped_text(record.get("created_by")),
            # The permission trees keep a Resident's own Workspace under it.
            "owned_workspace_id": stripped_text(record.get("owned_workspace_id")),
        },
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
        deleted_at=record.get("deleted_at"),
    )
    return {"descriptor": descriptor}


def _lineage_resource_projection(
    lineage: Mapping[str, Any],
    *,
    updated_by: object = "",
) -> dict[str, Any]:
    lineage_id = stripped_text(lineage.get("lineage_id"))
    identity_contract.validate_id(lineage_id, "lineage")
    nodes = [item for item in lineage.get("nodes") or [] if isinstance(item, Mapping)]
    relations = [
        _relation("member", item.get("resident_id"))
        for item in nodes
        if stripped_text(item.get("resident_id"))
    ]
    descriptor = _descriptor(
        lineage_id,
        title=lineage.get("title"),
        summary=lineage.get("description"),
        status="deleted" if stripped_text(lineage.get("deleted_at")) else "active",
        project_ids=[lineage.get("project_id")],
        relations=relations,
        attributes={
            "kind": stripped_text(lineage.get("kind")),
            "lineage_membership": stripped_text(lineage.get("lineage_membership")),
            "leader_resident_id": stripped_text(lineage.get("leader_resident_id")),
            "created_by": stripped_text(lineage.get("created_by")),
            # A personal Lineage outside every Project inherits from nothing:
            # only its owners' credentials cover it (decision_bd5217d08c09).
            **(
                {"permission_parents": []}
                if stripped_text(lineage.get("lineage_membership")) == "personal" and not stripped_text(lineage.get("project_id"))
                else {}
            ),
        },
        created_at=lineage.get("created_at"),
        updated_at=lineage.get("updated_at"),
        deleted_at=lineage.get("deleted_at"),
    )
    return {"descriptor": descriptor}


def _family_resource_projection(family: Mapping[str, Any]) -> dict[str, Any]:
    """A Family is a resource in its Lineage; the facade remains a Resident."""
    resource_id = stripped_text(family.get("resource_id"))
    identity_contract.validate_id(resource_id, "family")
    lineage_id = family["lineage_id"]
    relations = [_relation("lineage", lineage_id)]
    relations.extend(_relation("member", rid) for rid in family.get("members") or [])
    relations.extend(_relation(role, family[role]) for role in ("facade", "router", "steward") if family.get(role))
    return {"descriptor": _descriptor(
        resource_id, title=family["title"], summary=family.get("goal"),
        status="deleted" if family.get("deleted_at") else "active",
        project_ids=[family.get("project_id")], relations=relations,
        attributes={
            "family_id": family["family_id"], "function_block_id": family["family_id"],
            "lineage_id": lineage_id, "permission_parents": [lineage_id],
            "lineage_membership": family.get("lineage_membership", ""),
            "personal_owner_id": (family.get("lineage_created_by", "")
                                  if family.get("lineage_membership") == "personal" else ""),
            "member_resident_ids": list(family.get("members") or []),
            **{role + "_resident_id": family.get(role, "") for role in ("facade", "steward", "router")},
            "lineage_scope": family.get("lineage_scope", False),
            "route_threshold": family.get("route_threshold", 60),
            "review_interval_seconds": family.get("review_interval_seconds", 86400),
            "auto_migrate": family.get("auto_migrate", True),
            "auto_migrate_priority": family.get("auto_migrate_priority", []),
        }, created_at=family.get("created_at"), updated_at=family.get("updated_at"),
        deleted_at=family.get("deleted_at"),
    )}


def sync_families(resource_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Publish the current canonical descriptors of these Families."""
    from .state_store import get_lineage_data_store
    from . import family_storage
    with get_lineage_data_store().database.snapshot() as connection:
        families = family_storage.resource_records(connection, resource_ids=list(resource_ids))
    return _sync_many(_family_resource_projection(family) for family in families)


def sync_lineage(lineage: Mapping[str, Any], *, updated_by: object = "") -> dict[str, Any]:
    projection = _lineage_resource_projection(lineage, updated_by=updated_by)
    from .state_store import get_lineage_data_store
    from . import family_storage
    with get_lineage_data_store().database.snapshot() as connection:
        families = family_storage.resource_records(connection, lineage_id=lineage["lineage_id"])
    projections = [projection, *(_family_resource_projection(family) for family in families)]
    resources = _sync_many(projections)
    return resources[0]


def _resident_resource_bundle_projections(
    record: Mapping[str, Any],
    *,
    updated_by: object = "",
) -> list[dict[str, dict[str, Any]]]:
    resident_id = stripped_text(record.get("resident_id"))
    deleted_at = stripped_text(record.get("deleted_at"))
    projections = [
        _resident_resource_projection(record, updated_by=updated_by)
    ]
    # Workspace lifecycle is committed in its typed store by the deletion
    # owner, never replayed from an asynchronous Resident snapshot.
    return projections


def _merge_collaboration_projection(
    current: dict[str, dict[str, Any]],
    incoming: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    descriptor = dict(current["descriptor"])
    incoming_descriptor = incoming["descriptor"]
    active = (
        stripped_text(descriptor.get("status")) != "deleted"
        or stripped_text(incoming_descriptor.get("status")) != "deleted"
    )
    if active and stripped_text(descriptor.get("status")) == "deleted":
        descriptor.update(incoming_descriptor)
    descriptor["status"] = "active" if active else "deleted"
    descriptor["deleted_at"] = "" if active else max(
        stripped_text(descriptor.get("deleted_at")),
        stripped_text(incoming_descriptor.get("deleted_at")),
    )
    descriptor["updated_at"] = max(
        stripped_text(descriptor.get("updated_at")),
        stripped_text(incoming_descriptor.get("updated_at")),
    )
    relations: dict[tuple[str, str], dict[str, str]] = {}
    for relation in [
        *(descriptor.get("relations") or []),
        *(incoming_descriptor.get("relations") or []),
    ]:
        if not isinstance(relation, Mapping):
            continue
        kind = stripped_text(relation.get("kind"))
        target_id = stripped_text(relation.get("target_id"))
        if kind and target_id:
            relations[(kind, target_id)] = {"kind": kind, "target_id": target_id}
    descriptor["relations"] = list(relations.values())

    return {"descriptor": descriptor}


def sync_resident_resource_bundles(
    records: Iterable[Mapping[str, Any]],
    *,
    updated_by: object = "society-service",
) -> int:
    """Project canonical Resident snapshots and their Workspace children."""

    resident_count = 0
    projections_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        for projection in _resident_resource_bundle_projections(
            record,
            updated_by=updated_by,
        ):
            resource_id = stripped_text(projection["descriptor"].get("resource_id"))
            current = projections_by_id.get(resource_id)
            projections_by_id[resource_id] = (
                _merge_collaboration_projection(current, projection)
                if current is not None
                else projection
            )
        resident_count += 1
    projections = list(projections_by_id.values())
    for offset in range(0, len(projections), 100):
        batch = projections[offset : offset + 100]
        resources = _sync_many(batch)
        if len(resources) != len(batch):
            raise RuntimeError(
                "ResourceService returned an incomplete Resident projection batch"
            )
    return resident_count

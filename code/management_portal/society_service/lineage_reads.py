"""Lineage read models: listings, the workbench view, monitor definitions, message counts and node metadata."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from management_portal.portal_access_control import get_portal_access_store

from . import business_fact_views
from . import lineage_read_cache
from .lineage_program_runtime_projection import (
    _program_invoke_wire_runtime_view,
    _program_event_source_runtime_view,
    _program_effect_runtime_view,
)
from .lineage_values import _clone_json, _safe_actor, _text
from . import lineage_access_policy, lineage_view_projection
from .lineage_access_policy import (
    actor_can_manage_lineage as _actor_can_manage_lineage,
    actor_can_manage_lineages as _actor_can_manage_lineages,
)
from .lineage_view_projection import node_resident_id as _node_resident_id
from . import (
    identity_contract,
    lineage_execution_explanation,
    lineage_program_addresses,
    resident_catalog,
    resident_protocol,
)
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state


# The Lineage buttons a listing reports in ``allows``; each needs only ``lineage_id`` under the default scheme.
LINEAGE_ACTIONS = ("UpdateLineage", "DeleteLineage", "CreateResident", "UpdateResident", "CreateWorkspaceHandoff")


def _project_canonical_resident_titles(
    lineage: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> None:
    """Project each active Resident's canonical title and Provider into its node."""

    del snapshot
    nodes = [
        node
        for node in lineage.get("nodes") or []
        if isinstance(node, dict)
    ]
    records = lineage_state._resident_targets([_node_resident_id(node) for node in nodes])
    for node in nodes:
        record = records.get(_node_resident_id(node), {})
        title = _text(record.get("title"), 120)
        if title:
            node["title"] = title
            node["resident_title"] = title
        backend = _text(record.get("backend"), 40)
        if backend:
            node["backend"] = backend


def _lineage_summary(
    lineage: dict[str, Any],
    *,
    graph_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if graph_summary is None:
        statuses: dict[str, int] = {}
        for node in lineage.get("nodes") or []:
            status = str(node.get("status") or "idle")
            statuses[status] = statuses.get(status, 0) + 1
        graph_summary = {
            "lineage_node_ids": [
                _node_resident_id(node)
                for node in lineage.get("nodes") or []
                if isinstance(node, dict) and _node_resident_id(node)
            ],
            "node_count": len(lineage.get("nodes") or []),
            "edge_count": len(lineage.get("edges") or []),
            "active_seed_count": sum(
                1
                for seed in (lineage.get("program") or {}).get("components") or []
                if isinstance(seed, dict)
                and str(seed.get("kind") or "") == "seed"
                and str((seed.get("config") or {}).get("status") or "active") == "active"
            ),
            "statuses": statuses,
        }
    return {
        "lineage_id": str(lineage.get("lineage_id") or ""),
        **({"principal_hierarchy": _clone_json(lineage["principal_hierarchy"])} if "principal_hierarchy" in lineage else {}),
        "lineage_membership": lineage_state._lineage_membership(lineage),
        "project_id": _text(lineage.get("project_id"), 120),
        "title": str(lineage.get("title") or ""),
        "description": str(lineage.get("description") or ""),
        "leader_resident_id": str(lineage.get("leader_resident_id") or ""),
        **graph_summary,
        "revision": int(lineage.get("revision") or 0),
        "layout_revision": int(lineage.get("layout_revision") or 0),
        "runtime_revision": int(lineage.get("runtime_revision") or 0),
        "created_at": str(lineage.get("created_at") or ""),
        "created_by": str(lineage.get("created_by") or ""),
        "updated_at": str(lineage.get("updated_at") or ""),
        "graph_updated_at": str(
            lineage.get("graph_updated_at")
            or lineage.get("updated_at")
            or ""
        ),
        "layout_updated_at": str(lineage.get("layout_updated_at") or ""),
        "runtime_updated_at": str(lineage.get("runtime_updated_at") or ""),
    }


def get_lineage_program_resource_projection(
    lineage_id: str, suffix: str,
) -> dict[str, Any]:
    namespace, item_id = lineage_program_addresses.parse_program_resource_suffix(suffix)
    definition, _runtime = lineage_aggregate.lineage_catalog_store().get_state(lineage_id)
    _, _, item = lineage_program_addresses.find_program_resource(
        definition, suffix,
    )
    return {
        "schema": "tfi.lineage-program-resource.v1",
        "resource_id": lineage_id,
        "namespace": namespace,
        "item_id": item_id,
        "revision": definition.get("revision", 0),
        "item": item,
    }


def get_family_resource_projection(resource_id: str) -> dict[str, Any]:
    identity_contract.validate_id(resource_id, "family")
    from . import family_storage
    with lineage_aggregate.lineage_catalog_store().state_store.database.snapshot() as connection:
        records = family_storage.resource_records(connection, resource_ids=[resource_id])
    if not records or records[0].get("deleted_at"):
        raise KeyError(resource_id)
    return {"schema": "tfi.family-resource.v1", **records[0]}


def get_lineage_resource_projection(lineage_id: str) -> dict[str, Any]:
    clean_id = str(lineage_id or "").strip()
    lineage = lineage_aggregate.read_lineage_state(clean_id)
    return lineage_view_projection.resource_projection(
        clean_id, lineage, lineage_state._lineage_membership(lineage)
    )


def _lineage_read_fingerprint(lineage: dict[str, Any]) -> tuple[tuple[str, int, int], ...]:
    return lineage_read_cache.definition_fingerprint(
        lineage,
        seed_folder=lineage_state._seed_folder,
        node_resident_id=_node_resident_id,
    )


def _lineage_read_cache_key(lineage_id: str, actor: str) -> tuple[str, str, str]:
    return lineage_read_cache.cache_key(lineage_aggregate.DATA_ROOT, lineage_id, actor)


def _lineage_snapshot_for_read(
    lineage_id: str,
    actor: str,
) -> dict[str, Any]:
    """Capture canonical Lineage facts and add the authenticated read projection."""

    actor_id = _safe_actor(actor)
    lineage = _clone_json(lineage_aggregate.read_lineage_state(lineage_id))
    lineage = business_fact_views.canonical_view(
        lineage,
        resource_id=lineage_id,
        actor=actor_id,
    )
    lineage_state._ensure_visible(lineage, actor_id)
    return lineage


def get_lineage_definition_for_analysis(lineage_id: str, actor: str) -> dict[str, Any]:
    """Return an authorized definition without building the runtime UI view."""

    return _lineage_snapshot_for_read(lineage_id, actor)


def _read_projection_ports() -> lineage_read_cache.ReadProjectionPorts:
    return lineage_read_cache.ReadProjectionPorts(
        safe_actor=_safe_actor,
        snapshot_for_read=_lineage_snapshot_for_read,
        cache_key=_lineage_read_cache_key,
        fingerprint=_lineage_read_fingerprint,
        cache=lineage_state._LINEAGE_READ_CACHE,
        clone_json=_clone_json,
        project_titles=_project_canonical_resident_titles,
        verified_view=lineage_state._verified_lineage_view,
    )


def _current_lineage_view_for_read(lineage_id: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return lineage_read_cache.current_lineage_view_for_read(lineage_id, actor, ports=_read_projection_ports())


def list_lineages(actor: str) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    can_manage = _actor_can_manage_lineages(actor_id)
    catalog = lineage_aggregate.lineage_catalog_store()
    hydrated = business_fact_views.canonical_views(
        catalog.listing_records(),
        id_field="lineage_id",
        actor=actor_id,
    )
    visible = [
        payload for payload in hydrated
        if _actor_can_manage_lineage(payload, actor_id, operation="read")
    ] if can_manage else []
    graph_summaries = catalog.listing_graph_summaries([
        str(payload["lineage_id"]) for payload in visible
    ])
    owners = {
        str(payload["lineage_id"]): payload.get("created_by") if lineage_access_policy.is_personal_lineage(payload) else ""
        for payload in visible
    }
    # Each button asks only its own instruction (decision_b820faf42218 §3.1).
    passing = {name: lineage_access_policy.changeable_ids(actor_id, owners, name, "lineage_id") for name in LINEAGE_ACTIONS}
    lineages = [
        {**_lineage_summary(payload, graph_summary=graph_summaries[str(payload["lineage_id"])]),
         "allows": {name: str(payload["lineage_id"]) in ids for name, ids in passing.items()}}
        for payload in visible
    ]
    lineages.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return {"schema_version": lineage_state.SCHEMA_VERSION, "lineages": lineages}


# Read by the Resident Context service over the SocietyService socket.


def list_resident_discovery_catalog(actor: str) -> dict[str, Any]:
    """Return active registered Residents that can take work to an indexer."""

    actor_id = _safe_actor(actor)
    if not get_portal_access_store().is_service_actor(actor_id):
        raise PermissionError("Resident Discovery catalog requires a service actor")
    records: dict[str, dict[str, Any]] = {}
    for catalog_item in lineage_aggregate.lineage_catalog_store().snapshot():
        lineage_id = str(catalog_item.get("lineage_id") or "").strip()
        if not lineage_id:
            continue
        try:
            lineage = lineage_aggregate.read_lineage_state(lineage_id)
        except (KeyError, ValueError):
            continue
        lineage_title = str(lineage.get("title") or lineage_id)
        for node in lineage.get("nodes") or []:
            if not isinstance(node, Mapping):
                continue
            resident_id = _node_resident_id(node)
            if not resident_id:
                continue
            records[resident_id] = {
                "resident_id": resident_id,
                "lineage_id": lineage_id,
                "lineage_title": lineage_title,
                "lineage_description": str(lineage.get("description") or ""),
                "project_id": str(lineage.get("project_id") or ""),
                "title": str(node.get("title") or resident_id),
                "origin_seed_id": str(node.get("origin_seed_id") or ""),
                "status": str(node.get("status") or ""),
                "backend": str(node.get("backend") or ""),
                "created_at": str(node.get("created_at") or ""),
                "updated_at": str(node.get("updated_at") or ""),
                "lineage_revision": int(lineage.get("revision") or 0),
                "lineage_refresh_signature": str(lineage.get("updated_at") or ""),
            }
    active_ids = {
        record["resident_id"]
        for record in resident_catalog.get_catalog().get_many(records)
        if resident_catalog.registration_ready(record)
    }
    return {
        "schema": "tfi.resident-discovery-catalog.v1",
        "residents": [records[key] for key in sorted(records) if key in active_ids],
    }


def list_removed_discovery_residents(actor: str, resident_ids: list[str]) -> dict[str, Any]:
    """Of ``resident_ids``, those whose removal is recorded.

    A Resident is removed once it or its Lineage is deleted, or no record of it
    remains. One only missing from the catalog because its Lineage could not
    be read is not removed.
    """

    actor_id = _safe_actor(actor)
    if not get_portal_access_store().is_service_actor(actor_id):
        raise PermissionError("Resident Discovery catalog requires a service actor")
    requested = sorted({str(item or "").strip() for item in resident_ids or [] if str(item or "").strip()})
    live = resident_catalog.get_catalog().state_store.live_resident_ids(requested)
    return {
        "schema": "tfi.resident-discovery-removed.v1",
        "resident_ids": [resident_id for resident_id in requested if resident_id not in live],
    }


def list_lineage_human_notifications(
    actor: str,
    lineage_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return actor-visible Lineage notification facts without UI metadata."""

    actor_id = _safe_actor(actor)
    requested = (
        {
            _text(lineage_id, 80).lower()
            for lineage_id in lineage_ids
            if _text(lineage_id, 80)
        }
        if lineage_ids is not None
        else None
    )
    from .lineage_human_notifications import list_actor_notifications

    definitions = list_actor_notifications(actor_id, requested)
    portal_level = [item for item in definitions if not item.get("lineage_id")]
    visible = business_fact_views.canonical_views(
        [item for item in definitions if item.get("lineage_id")],
        id_field="lineage_id",
        actor=actor_id,
    )
    result: list[dict[str, Any]] = []
    for lineage in [*visible, *portal_level]:
        lineage_id = str(lineage.get("lineage_id") or "").strip()
        notifications = lineage["notifications"]
        if notifications:
            result.append({
                "lineage_id": lineage_id,
                "title": str(lineage.get("title") or ""),
                "notifications": notifications,
            })
    return {"lineages": result}


def actor_lineage_access_records(
    actor: str,
    lineage_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the Lineage membership facts authorized for one actor."""

    actor_id = _safe_actor(actor)
    catalog = lineage_aggregate.lineage_catalog_store()
    definitions = (
        catalog.snapshot()
        if lineage_ids is None
        else catalog.get_access_records(lineage_ids)
    )
    records: list[dict[str, Any]] = []
    for lineage in definitions:
        lineage_id = str(lineage.get("lineage_id") or "")
        if not _actor_can_manage_lineage(lineage, actor_id, operation="read"):
            continue
        node_ids = (
            list(dict.fromkeys(
                str(item or "").strip()
                for item in lineage.get("lineage_node_ids") or []
                if str(item or "").strip()
            ))
            if lineage_ids is not None
            else list(dict.fromkeys(
                _node_resident_id(node)
                for node in lineage.get("nodes") or []
                if isinstance(node, Mapping) and _node_resident_id(node)
            ))
        )
        records.append({
            "lineage_id": lineage_id,
            "lineage_membership": lineage_state._lineage_membership(lineage),
            "project_id": _text(lineage.get("project_id"), 120),
            "lineage_node_ids": node_ids,
            "leader_resident_id": str(
                lineage.get("leader_resident_id") or ""
            ).strip(),
        })
    return records


def get_lineage_monitor_definition(lineage_id: str, actor: str) -> dict[str, Any]:
    """Read authorized flow definitions without assembling the workbench view."""

    actor_id = _safe_actor(actor)
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_state._ensure_visible(lineage, actor_id)
    program = lineage.get("program") or {}
    return {
        "lineage_id": lineage["lineage_id"],
        "title": lineage.get("title"),
        "revision": lineage.get("revision"),
        "nodes": [
            {"id": node.get("id"), "title": node.get("title")}
            for node in lineage.get("nodes") or [] if isinstance(node, dict)
        ],
        "program": deepcopy({
            "components": program.get("components") or [],
            "wires": program.get("wires") or [],
        }),
    }


def get_lineage_monitor_definitions(lineage_ids: list[str], actor: str) -> dict[str, Any]:
    """Read independent monitor definitions in one RPC/read-only snapshot."""

    actor_id = _safe_actor(actor)
    requested = list(dict.fromkeys(str(value).strip() for value in lineage_ids if str(value).strip()))
    definitions: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    if requested:
        # Existing canonical readers join this snapshot. Keep each Lineage's
        # access/deletion checks and isolate its domain validation errors.
        with lineage_aggregate.lineage_catalog_store().state_store.database.snapshot():
            for lineage_id in requested:
                try:
                    definitions[lineage_id] = get_lineage_monitor_definition(lineage_id, actor_id)
                except Exception as exc:  # noqa: BLE001 - independent read results
                    errors[lineage_id] = str(exc)
    return {"definitions": definitions, "lineage_errors": errors}


def get_lineage(lineage_id: str, actor: str) -> dict[str, Any]:
    _lineage, view = _current_lineage_view_for_read(
        lineage_id,
        actor,
    )
    invoke_runtime = _program_invoke_wire_runtime_view(view)
    view["program_invoke_wire_runtime"] = invoke_runtime
    event_runtime = _program_event_source_runtime_view(view)
    view["program_event_source_runtime"] = event_runtime
    # Live User availability is independent of the cached definition revision.
    from .lineage_program_runtime_projection import event_source_diagnostics
    view.setdefault("program_diagnostics", []).extend(event_source_diagnostics(event_runtime))
    view["program_effect_runtime"] = _program_effect_runtime_view(view)
    view["program_execution_explanations"] = (
        lineage_execution_explanation.explain_program(
            view,
            event_source_runtime=event_runtime,
            invoke_wire_runtime=invoke_runtime,
        )
    )
    view["refresh_signature"] = lineage_read_cache.definition_refresh_signature(view)
    return view


def _lineage_node_session_key(
    node: dict[str, Any],
) -> str:
    return str(node.get("id") or "")


def _node_user_message_count(node: dict[str, Any], actor: str) -> int:
    return max(0, int(resident_domain.get_resident_user_message_count(
        _lineage_node_session_key(node),
        actor,
    )))


def get_node_message_counts(lineage_id: str, actor: str) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    lineage = get_lineage(lineage_id, actor_id)
    session_keys = tuple(
        str(node.get("id") or "")
        for node in lineage.get("nodes") or []
        if isinstance(node, dict) and str(node.get("id") or "")
    )
    state_snapshots = resident_protocol.read_resident_state_snapshots(
        session_keys
    )
    fingerprint = (
        int(lineage.get("revision") or 0),
        tuple(
            (
                resident_id,
                int((state_snapshots.get(resident_id) or {}).get("cursor") or 0),
            )
            for resident_id in session_keys
        ),
    )
    cache_key = _lineage_read_cache_key(lineage_id, actor_id)
    with lineage_state._LINEAGE_READ_CACHE.build_lock("message_counts", cache_key):
        cached = lineage_state._LINEAGE_READ_CACHE.get("message_counts", cache_key, fingerprint)
        if cached:
            return _clone_json(cached["payload"])
        payload = _build_node_message_counts_payload(lineage_id, actor_id, lineage)
        lineage_state._LINEAGE_READ_CACHE.put("message_counts", cache_key, {
            "fingerprint": fingerprint,
            "payload": _clone_json(payload),
        })
        return payload


def _build_node_message_counts_payload(
    lineage_id: str,
    actor_id: str,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    nodes = [node for node in lineage.get("nodes") or [] if isinstance(node, dict)]
    session_keys = {_lineage_node_session_key(node) for node in nodes}
    resolved: dict[str, tuple[bool, int]] = {}
    context_pressures: dict[str, dict[str, Any]] = {}
    counts = resident_domain.get_resident_user_message_counts(list(session_keys), actor_id)
    context_pressures = resident_domain.get_resident_context_pressures(
        list(session_keys),
        actor_id,
    )
    resolved = {
        key: (key in counts, max(0, int(counts.get(key) or 0)))
        for key in session_keys
    }
    sessions: list[dict[str, Any]] = []
    for node in nodes:
        key = _lineage_node_session_key(node)
        if key not in resolved:
            try:
                resolved[key] = (True, _node_user_message_count(node, actor_id))
            except (KeyError, PermissionError, ValueError):
                resolved[key] = (False, 0)
        available, user_message_count = resolved[key]
        sessions.append({
            "resident_id": str(node.get("id") or ""),
            "user_message_count": user_message_count,
            "available": available,
            "context_pressure": _clone_json(context_pressures.get(key) or {}),
        })
    return {
        "schema_version": lineage_state.SCHEMA_VERSION,
        "lineage_id": str(lineage.get("lineage_id") or lineage_id),
        "sessions": sessions,
    }


def get_lineage_node_metadata(
    lineage_id: str,
    resident_id: str,
    actor: str,
) -> dict[str, Any]:
    """Authorize and return the lightweight session projection for one node."""

    actor_id = _safe_actor(actor)
    target_id = _text(resident_id, 80).lower()
    try:
        # Node membership belongs to the immutable Lineage definition.  A
        # committed PostgreSQL read is sufficient and must not queue behind an
        # unrelated aggregate writer or materialize runtime state.
        lineage = lineage_aggregate.lineage_catalog_store().get(lineage_id)
    except KeyError:
        raise KeyError("Lineage not found") from None
    node = next(
        (
            item
            for item in lineage.get("nodes") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == target_id
        ),
        None,
    )
    if node is None:
        raise KeyError("Lineage node not found")
    if not target_id:
        raise ValueError("Lineage node is not bound to a Resident session")

    # The visibility check may call ResourceService; its socket must not
    # extend the lifetime of the Lineage aggregate lock.
    lineage_state._ensure_visible(lineage, actor_id)


    detail = resident_domain.get_lineage_node_metadata(
        target_id,
        actor_id,
    )
    detail["lineage_id"] = lineage_id
    detail["lineage_resident_id"] = target_id
    return detail

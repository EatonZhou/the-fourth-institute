"""What Web routes ask the resource system: index change hints, Portal locations and
reference payloads of addresses, the existence check of page routes and the
readable part of a knowledge graph.
"""
from __future__ import annotations

import json
import sys
from urllib.parse import unquote

from management_portal import conversation_index_projection
from management_portal.resources import get_resource_service, ResourceService
from management_portal.resources.portal import canonical_portal_location
from management_portal.society_service import SocietyServiceUnavailable


def _notify_resource_index(
    *,
    path: str = "",
    resource_id: str = "",
    resource_type: str = "",
    kind: str = "unknown",
    wait: bool = False,
    timeout: float = 60.0,
) -> dict[str, object]:
    try:
        return get_resource_service().notify_source_changed(
            path=path,
            resource_id=resource_id,
            resource_type=resource_type,
            kind=kind,
            wait=wait,
            timeout=timeout,
        )
    except Exception as exc:
        # Source writes remain authoritative even when an optional index hint
        # cannot be queued. Low-frequency reconciliation will recover it.
        print(
            json.dumps(
                {
                    "event": "resource_change_notification_failed",
                    "path": path,
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "kind": kind,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return {"accepted": False, "error": str(exc)}


def _resource_portal_location(address: object, actor: object = "") -> str:
    clean_address = unquote(str(address or "")).strip().strip("/")
    if not clean_address:
        return ""
    fallback = (
        ResourceService.portal_url_for_address(clean_address)
        if "/" not in clean_address else ""
    )
    try:
        reference = get_resource_service().reference(
            clean_address,
            actor=str(actor or ""),
        )
    except (KeyError, FileNotFoundError, PermissionError, RuntimeError, ValueError):
        return fallback
    return canonical_portal_location(clean_address, reference.get("url")) or fallback


def _resource_reference_payload(address: str, actor: str) -> dict:
    reference = dict(get_resource_service().reference(address, actor=actor))
    if str(reference.get("resource_type") or "").strip() != "resident":
        return reference
    resident_id = str(reference.get("resource_id") or "").strip()
    if not resident_id:
        return reference
    try:
        navigation = conversation_index_projection.get_resident_navigation(
            resident_id,
            actor=actor,
        )
    except SocietyServiceUnavailable:
        raise
    except (KeyError, PermissionError, RuntimeError, ValueError):
        return reference
    navigation_url = (
        str(navigation.get("url") or "").strip()
        if isinstance(navigation, dict)
        else ""
    )
    if navigation_url:
        reference["url"] = navigation_url
    return reference


# Page routes check only who calls and that the named resource exists;
# permissions belong to the permission system (decision_b820faf42218).
def _require_resource(resource_id: str, actor: str) -> dict:
    if not str(actor or "").strip():
        raise PermissionError("需要登录")
    return get_resource_service().get(resource_id, actor=actor)


def _filter_resource_graph(payload: dict, actor: str) -> dict:
    def node_resource_id(node: dict) -> str:
        return str(
            node.get("resource_id")
            or node.get("kb_id")
            or node.get("id")
            or ""
        ).strip()

    raw_nodes = [
        dict(node)
        for node in payload.get("nodes") or []
        if isinstance(node, dict)
    ]
    target_ids = {node_resource_id(node) for node in raw_nodes}
    target_ids.discard("")

    readable_ids: set[str] = set()
    offset = 0
    page_size = 5000
    while target_ids - readable_ids:
        page = get_resource_service().query(
            facets=["knowledge"],
            actor=str(actor or ""),
            limit=page_size,
            offset=offset,
            include_attributes=False,
        )
        resources = [
            item
            for item in page.get("resources") or []
            if isinstance(item, dict)
        ]
        readable_ids.update(
            resource_id
            for item in resources
            if (
                resource_id := str(item.get("resource_id") or "").strip()
            ) in target_ids
        )
        if not resources:
            break
        offset += len(resources)
        try:
            total = int(page.get("total"))
        except (TypeError, ValueError):
            total = offset
        if offset >= total:
            break

    graph = dict(payload)
    nodes = [
        node
        for node in raw_nodes
        if node_resource_id(node) in readable_ids
    ]
    node_ids = {node_resource_id(node) for node in nodes}
    edges = [
        dict(edge)
        for edge in payload.get("edges") or []
        if isinstance(edge, dict)
        and str(edge.get("source") or "") in node_ids
        and str(edge.get("target") or "") in node_ids
    ]
    if any("degree" in node for node in nodes):
        degree = {resource_id: 0 for resource_id in node_ids}
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            degree[source] += 1
            if target != source:
                degree[target] += 1
        for node in nodes:
            node["degree"] = degree[node_resource_id(node)]
        nodes.sort(
            key=lambda node: (
                -int(node.get("degree") or 0),
                -int(node.get("importance") or 0),
                str(node.get("title") or "").lower(),
            )
        )
    graph["nodes"] = nodes
    graph["edges"] = edges
    if "relation_counts" in graph:
        counts: dict[str, int] = {}
        for edge in edges:
            relation = str(edge.get("relation") or "")
            if relation:
                counts[relation] = counts.get(relation, 0) + 1
        graph["relation_counts"] = counts
    unresolved_by_source = graph.pop("_unresolved_links_by_source", None)
    if isinstance(unresolved_by_source, dict):
        unresolved_links = 0
        for resource_id in node_ids:
            try:
                unresolved_links += max(
                    0,
                    int(unresolved_by_source.get(resource_id) or 0),
                )
            except (TypeError, ValueError):
                continue
        graph["unresolved_links"] = unresolved_links
    elif len(nodes) != len(raw_nodes) and "unresolved_links" in graph:
        graph["unresolved_links"] = 0
    return graph

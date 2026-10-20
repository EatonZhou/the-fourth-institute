from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import parse_qs, quote, unquote, urlsplit

from management_common.resource_identity import get_resource_type_registry

KNOWLEDGE_ENTRY_TYPES = frozenset({
    "meeting", "question", "idea", "insight", "decision", "plan", "thinking",
    "risk", "concept", "information", "patent", "material", "todo", "review",
})
RESOURCE_PORTAL_ROUTES = {
    **{kind: ("/resource/knowledge", "item") for kind in KNOWLEDGE_ENTRY_TYPES},
    **{kind: (f"/resource/{kind}", "item") for kind in ("skill", "repo", "article", "experiment", "device")},
    "paper": ("/paper", "paper"),
    "test": ("/wall", "case"),
    "resident": ("/lineage", "resident"),
    "workspace": ("/lineage", "workspace"),
    "lineage": ("/lineage", "lineage"),
    "family": ("/lineage", "family"),
    "chatroom": ("/lineage", "chatroom"),
    "workbench": ("/workbenches/view", "id"),
    "project": ("/projects/", "project"),
}
RESOURCE_PORTAL_CONTEXT = {"resident": {"lineage": "lineage"},
                           "family": {"lineage": "lineage"}}
NAVIGATION_RESOURCE_TYPES = frozenset({"resident", "lineage", "test", "workbench", "workspace", "family"})
NAVIGATION_REFERENCE_ROUTES = {
    "testrun": {
        "pattern": r"testrun_[0-9a-f]{16}",
        "path": "/wall",
        "parameter": "run",
        "title": "Test Run",
    },
}


def portal_route_contract() -> dict:
    identity_parameters = {
        parameter for _, parameter in RESOURCE_PORTAL_ROUTES.values()
    } | {
        route["parameter"] for route in NAVIGATION_REFERENCE_ROUTES.values()
    } | {"semantic"}
    return {
        "schema": "tfi.resource-portal-routes.v1",
        "routes": [
            {
                "resource_type": kind,
                "path": path,
                "parameter": parameter,
                "context": RESOURCE_PORTAL_CONTEXT.get(kind, {}),
                "detail_kind": "knowledge" if kind in KNOWLEDGE_ENTRY_TYPES else kind,
                "embed_parameter": (
                    "embedded"
                    if kind in KNOWLEDGE_ENTRY_TYPES | {"skill", "repo", "article", "experiment", "device"}
                    else ""
                ),
            }
            for kind, (path, parameter) in sorted(RESOURCE_PORTAL_ROUTES.items())
        ],
        "identity_parameters": sorted(identity_parameters),
        "content_route": {"path": "/api/resources/content", "parameter": "address"},
        "navigation_resource_types": sorted(NAVIGATION_RESOURCE_TYPES),
        "navigation_references": [
            {"reference_type": reference_type, **route}
            for reference_type, route in sorted(NAVIGATION_REFERENCE_ROUTES.items())
        ],
        "text_resource_types": sorted(KNOWLEDGE_ENTRY_TYPES | {"skill", "article", "document"}),
    }


def portal_route_script() -> bytes:
    payload = json.dumps(portal_route_contract(), ensure_ascii=True, sort_keys=True)
    return f"window.PortalResourceRoutes = Object.freeze({payload});\n".encode()


def portal_url_for_address(address: object, resource_type: object = "") -> str:
    """Return the canonical Portal detail surface for a known root resource."""
    clean_address = str(address or "").strip().strip("/")
    root_id = clean_address.partition("/")[0]
    try:
        parsed = get_resource_type_registry().parse(root_id)
    except (RuntimeError, ValueError):
        return ""
    inferred_type = parsed.resource_type
    clean_type = str(resource_type or inferred_type).strip()
    if clean_type != inferred_type:
        return ""
    route = RESOURCE_PORTAL_ROUTES.get(clean_type)
    if not route:
        return ""
    path, parameter = route
    return f"{path}?{parameter}={quote(root_id, safe='')}"


def portal_url_for_resource(
    resource_id: object,
    resource_type: object = "",
    *,
    record: Mapping | None = None,
) -> str:
    """Return the Portal surface for an indexed resource projection."""

    clean_id = str(resource_id or "").strip().strip("/")
    clean_type = str(resource_type or "").strip()
    metadata = dict(record or {})
    metadata.update(metadata.get("attributes") or {})
    if clean_type == "project" and clean_id:
        path, parameter = RESOURCE_PORTAL_ROUTES["project"]
        return f"{path}?{parameter}={quote(clean_id, safe='')}"
    if clean_type == "review" and metadata.get("paper_id"):
        return portal_url_for_address(metadata["paper_id"], "paper")
    if clean_type == "family" and metadata.get("lineage_id") and metadata.get("function_block_id"):
        return (f"{portal_url_for_address(metadata['lineage_id'], 'lineage')}"
                f"&program_item={quote(str(metadata['function_block_id']), safe='')}"
                f"&family={quote(clean_id, safe='')}")
    if clean_type == "search" and metadata.get("surface"):
        return portal_url_for_search(clean_id, metadata)
    return portal_url_for_address(clean_id, clean_type)


def portal_url_for_search(search_id: str, record: Mapping) -> str:
    encoded = quote(search_id, safe="")
    surface = str(record.get("surface") or "")
    if surface == "knowledge":
        scope = str(record.get("resource_scope") or "knowledge")
        path = "/resource/knowledge" if scope == "knowledge" else RESOURCE_PORTAL_ROUTES[scope][0]
    elif surface == "wall":
        return f"/wall?project={quote(str(record.get('project') or ''), safe='')}&semantic={encoded}"
    else:
        path = RESOURCE_PORTAL_ROUTES[surface][0].rstrip("/")
    return f"{path}?semantic={encoded}"


def canonical_portal_location(address: object, value: object) -> str:
    clean = str(value or "").strip()
    if not clean.startswith("/") or clean.startswith("//") or "\\" in clean or any(ord(char) < 32 for char in clean):
        return ""
    try:
        parsed = urlsplit(clean)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc:
        return ""
    resource_address = str(address or "").strip("/")
    root_id = resource_address.partition("/")[0]
    if not parsed.query and unquote(parsed.path).strip("/") in {resource_address, root_id}:
        return ""
    if parsed.path.startswith("/r/"):
        return ""
    if parsed.path.rstrip("/") in {"/resource", "/resource.html"} and parse_qs(parsed.query).get("address"):
        return ""
    return clean

"""Principal organisation within the existing Lineage aggregate, independent of grants.

A Lineage owns its HATG, Resident relationships and collaboration settings.
A Project-level Lineage includes references to child Lineage leaders in that
same model; references never move an identity out of its home Lineage.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

LEVELS = ("portal_admin", "user", "project", "lineage", "resident")
LINEAGE_LEVELS = frozenset({"user", "project", "lineage"})
FIELD = "principal_hierarchy"
SCHEMA = "tfi.principal-hierarchy.v1"


def lineage_scope(value: object, *, project_id: str = "") -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"level", "scope_id"}:
        raise ValueError("Lineage hierarchy requires level and scope_id")
    level, scope_id = value["level"], value["scope_id"]
    if level not in LINEAGE_LEVELS or not isinstance(scope_id, str) or (not scope_id.strip() and (level != "lineage" or project_id)):
        raise ValueError("Invalid Lineage hierarchy scope")
    if scope_id != scope_id.strip():
        raise ValueError("Hierarchy scope_id must be canonical")
    if level in {"project", "lineage"} and scope_id != project_id:
        raise ValueError("Project and Lineage hierarchy scope must match project_id")
    return {"level": level, "scope_id": scope_id}


def resident_assignment(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"level", "category", "scope_ids"}:
        raise ValueError("Resident hierarchy requires level, category and scope_ids")
    level = value["level"]
    # Portal administration is an effective User permission. Assigning a Resident
    # an organisation level must never create an administrator.
    if level not in {"user", "project", "lineage", "resident"}:
        raise ValueError("Invalid Resident principal level")
    category, scopes = value["category"], value["scope_ids"]
    if not isinstance(category, str) or not category.strip() or len(category) > 120:
        raise ValueError("Resident hierarchy requires a bounded category")
    if not isinstance(scopes, list) or any(not isinstance(s, str) or not s or s != s.strip() for s in scopes):
        raise ValueError("Resident hierarchy scope_ids must contain canonical IDs")
    if len(set(scopes)) != len(scopes):
        raise ValueError("Resident hierarchy scope_ids must be unique")
    return {"level": level, "category": category, "scope_ids": list(scopes)}


def scope_after_project_change(scope: Mapping[str, str], project_id: str) -> dict[str, str]:
    if scope["level"] == "project" and scope["scope_id"] != project_id:
        raise ValueError("Project principal cannot move to another Project")
    return {**scope, "scope_id": project_id} if scope["level"] == "lineage" else dict(scope)


def level_for_resident(record: Mapping[str, Any], home: Mapping[str, Any]) -> str:
    explicit = record.get(FIELD)
    scope = home.get(FIELD) or {}
    if scope.get("level") == "user" or home.get("lineage_membership") == "personal":
        return "user"
    if explicit is not None and resident_assignment(explicit)["level"] == "user":
        return "user"
    if record.get("resident_id") == home.get("leader_resident_id"):
        return "project" if scope.get("level") == "project" else "lineage"
    return "resident"


def project_leaders(
    graph: Mapping[str, Any], lineages: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Return Resident ID -> home Lineage ID for structural Project members."""
    if (graph.get(FIELD) or {}).get("level") != "project":
        return {}
    result: dict[str, str] = {}
    for child in lineages:
        if child["lineage_id"] == graph["lineage_id"] or child.get("project_id") != graph.get("project_id"):
            continue
        if child.get("lineage_membership") == "personal" or (child.get(FIELD) or {}).get("level") in {"project", "user"}:
            continue
        leader = str(child.get("leader_resident_id") or "")
        if leader:
            result[leader] = str(child["lineage_id"])
    return result


def project_node_access(view: dict[str, Any], records: Iterable[Mapping[str, Any]], can_read) -> None:
    """Project references expose topology, but never a denied child's content."""
    if (view.get(FIELD) or {}).get("level") != "project":
        return
    homes = {row["resident_id"]: row["lineage_id"] for row in records}
    for node in view.get("nodes", []):
        home = homes.get(node["id"], "")
        if home == view["lineage_id"]:
            continue
        accessible = bool(home and can_read(node["id"]) and can_read(home))
        if not accessible:
            position = {key: node[key] for key in ("id", "x", "y") if key in node}
            node.clear()
            node.update(position, title="无权访问的 Lineage", role={})
        node.update(hierarchy_reference=True, accessible=accessible)
        if accessible:
            node["home_lineage_id"] = home


def classify_existing(record: Mapping[str, Any], home: Mapping[str, Any], *, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Classify from registered entry-point evidence; titles are never authority."""
    scopes = list(dict.fromkeys(str(s) for s in evidence.get("scope_ids", []) if s))
    category = str(evidence.get("category") or "")
    # Resource conversations and platform capabilities live in the AI员工
    # Lineages of the Portal pages they serve; they are ordinary members there.
    user_categories = {
        "personal_ama", "resident_routing", "background_model", "background_manager", "shared_service",
    }
    if category in user_categories:
        return resident_assignment({"level": "user", "category": category, "scope_ids": scopes})
    level = level_for_resident(record, home)
    if level == "user":
        category = category or "personal_conversation"
    elif level == "project":
        category = "project_leader"
    elif level == "lineage":
        category = "lineage_leader"
    else:
        category = category or "lineage_member"
    return resident_assignment({"level": level, "category": category, "scope_ids": scopes})

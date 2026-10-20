"""Resolve ordinary shared Lineages using the Resource page taxonomy."""
from __future__ import annotations

from .application_bindings import resolve_application_lineage
from .resources.portal import KNOWLEDGE_ENTRY_TYPES

RESOURCE_PAGE_TYPES = frozenset({"article", "repo", "skill", "experiment", "device"})


def resource_lineage_scope(resource_type: str) -> str:
    kind = str(resource_type or "").strip().lower()
    if kind in KNOWLEDGE_ENTRY_TYPES:
        kind = "knowledge"
    elif kind not in RESOURCE_PAGE_TYPES:
        raise ValueError(f"Unsupported Resource page type: {resource_type!r}")
    return f"resource-chat:{kind}"


def resolve_resource_lineage(*, actor: str, resource_type: str) -> str:
    return resolve_application_lineage(
        actor=actor, application=resource_lineage_scope(resource_type),
    )

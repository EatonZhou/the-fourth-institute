"""Semantic search surfaces and the Residents that serve them.

Each surface (Paper, Knowledge, WALL …) has a Resident in semantic_search_residents.  Every
active Portal user may invoke it.
"""
from __future__ import annotations

# The resource catalog's surfaces (Knowledge, Skill, Repo, Article, Experiment).
RESOURCE_SCOPES = ("knowledge", "skill", "repo", "article", "experiment")
SURFACES = frozenset({*RESOURCE_SCOPES, "paper", "wall"})
INVOKE_POLICY = "active_portal_users"


def canonical_surface(value: object) -> str:
    surface = str(value or "").strip()
    if surface not in SURFACES:
        raise ValueError("unsupported semantic search surface")
    return surface


def search_binding(surface: object) -> dict[str, str]:
    """Configured Resident for this semantic search surface."""

    from . import application_targets

    holder = application_targets.search_resident(canonical_surface(surface))
    return {
        "surface": canonical_surface(surface),
        "lineage_id": holder["lineage_id"],
        "resident_id": holder["resident_id"],
        "invoke_policy": INVOKE_POLICY,
    }

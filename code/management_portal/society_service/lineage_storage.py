"""Lineage folders, computed from each Lineage's Project.

A Project Lineage lives at ``projects:<project>/agents/<lineage_id>``; a
Lineage without a Project lives at ``lineages:<lineage_id>``. No entry link
points at a folder: finding one means computing it from the Project.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from management_common.portal_paths import path as portal_path


LINEAGE_ID_RE = re.compile(r"^lineage_[0-9a-f]{12}$")
PROJECT_LINEAGE_DIRECTORY = "agents"


def _lineage_id(value: object) -> str:
    lineage_id = str(value or "").strip().lower()
    if not LINEAGE_ID_RE.fullmatch(lineage_id):
        raise ValueError("invalid Lineage id")
    return lineage_id


def lineages_root() -> Path:
    return portal_path("lineages")


def lineage_folder_for_project(lineage_id: object, project_id: str) -> Path:
    """Where a Lineage's folder belongs for this Project ('' means none)."""
    identity = _lineage_id(lineage_id)
    project = str(project_id or "").strip()
    if not project:
        return lineages_root() / identity
    # A Project's folder is fixed at projects:<Project ID> (its only name).
    from management_portal.project_registry import PROJECT_ID
    if not PROJECT_ID.fullmatch(project) or project in {".", ".."}:
        raise ValueError(f"invalid Project id: {project}")
    return portal_path("projects") / project / PROJECT_LINEAGE_DIRECTORY / identity


def lineage_project_ids(lineage_ids: Iterable[object], *, connection) -> dict[str, str]:
    """The registered Project of each Lineage, read on a Lineage-schema connection; unknown Lineages map to ''."""
    identities = list(dict.fromkeys(_lineage_id(value) for value in lineage_ids))
    if not identities:
        return {}
    rows = connection.execute(
        "SELECT lineage_id, project_id FROM lineages WHERE lineage_id = ANY(%s)",
        (identities,),
    ).fetchall()
    found = {row["lineage_id"]: str(row["project_id"] or "").strip() for row in rows}
    return {identity: found.get(identity, "") for identity in identities}


def lineage_folders(lineage_ids: Iterable[object], *, connection) -> dict[str, Path]:
    return {identity: lineage_folder_for_project(identity, project_id)
            for identity, project_id in lineage_project_ids(lineage_ids, connection=connection).items()}

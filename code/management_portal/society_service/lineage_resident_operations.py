"""Lineage-owned operations used by the Resident lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import lineage_domain, lineage_storage


@dataclass(frozen=True)
class LineageDeletionToken:
    lineage_id: str
    actor: str
    deleted_at: str
    folder: Path
    definition: dict[str, Any]


def canonical_lineage_folder(lineage_id: str) -> Path:
    return lineage_domain._lineage_dir(lineage_id)


def managed_lineage_definition(lineage_id: str, actor: str) -> dict[str, Any]:
    definition = lineage_domain._read_lineage(lineage_id, actor=actor)
    lineage_domain._ensure_manager(definition, actor)
    return definition




def bind_lineage_folder(lineage_id: str, target: Path) -> Path:
    return lineage_storage.bind_lineage_folder(
        lineage_id, target, folder_root=lineage_domain._lineages_root(),
    )


def prepare_lineage_deletion(
    lineage_id: str, actor: str, deleted_at: str, expected_folder: Path,
) -> LineageDeletionToken:
    definition = managed_lineage_definition(lineage_id, actor)
    folder = canonical_lineage_folder(lineage_id)
    if folder.resolve(strict=False) != expected_folder.resolve(strict=False):
        raise ValueError("Lineage Folder 与待删除工作区不一致")
    return LineageDeletionToken(lineage_id, actor, deleted_at, expected_folder, definition)


def lineage_deletion_effects(token: LineageDeletionToken) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "kind": "lineage_tombstone", "lineage_id": token.lineage_id,
            "definition": token.definition, "folder": str(token.folder),
            "actor": token.actor, "deleted_at": token.deleted_at,
        },
        {
            "kind": "lineage_folder_unbind", "lineage_id": token.lineage_id,
            "folder": str(token.folder),
        },
    )


def commit_lineage_deletion(token: LineageDeletionToken) -> None:
    lineage_domain._lineage_catalog_store().remove(token.lineage_id, deleted_by=token.actor)


def project_lineage_deletion(step: dict[str, Any]) -> None:
    lineage_domain._sync_deleted_lineage_resource_identity(
        step["definition"], step["actor"], step["deleted_at"],
    )


def unbind_deleted_lineage_folder(step: dict[str, Any]) -> None:
    lineage_storage.unbind_lineage_folder(
        step["lineage_id"], folder_root=lineage_domain._lineages_root(),
        expected_target=Path(step["folder"]),
    )


def finalize_deleted_resident_detach(lineage_id: str) -> None:
    lineage_domain._finalize_deleted_resident_projection(lineage_id)

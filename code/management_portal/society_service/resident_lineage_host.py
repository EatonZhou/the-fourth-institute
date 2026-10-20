from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import lineage_domain, lineage_resident_operations


class ResidentLineageHost:
    """Adapter exposing only the Lineage operations required by Residents."""

    @staticmethod
    def authorize_lineage_resident_access(lineage_id: str, resident_id: str) -> str:
        return lineage_domain.authorize_lineage_resident_access(lineage_id, resident_id)

    @staticmethod
    def canonical_lineage_folder(lineage_id: str) -> Path:
        return lineage_resident_operations.canonical_lineage_folder(lineage_id)

    @staticmethod
    def managed_lineage_definition(lineage_id: str, actor: str) -> dict[str, Any]:
        return lineage_resident_operations.managed_lineage_definition(lineage_id, actor)


    @staticmethod
    def bind_lineage_folder(lineage_id: str, target: Path) -> Path:
        return lineage_resident_operations.bind_lineage_folder(lineage_id, target)

    @staticmethod
    def prepare_lineage_deletion(
        lineage_id: str,
        actor: str,
        deleted_at: str,
        expected_folder: Path,
    ) -> lineage_resident_operations.LineageDeletionToken:
        return lineage_resident_operations.prepare_lineage_deletion(
            lineage_id, actor, deleted_at, expected_folder,
        )

    @staticmethod
    def lineage_deletion_effects(
        token: lineage_resident_operations.LineageDeletionToken,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return lineage_resident_operations.lineage_deletion_effects(token)

    @staticmethod
    def commit_lineage_deletion(token: lineage_resident_operations.LineageDeletionToken) -> None:
        lineage_resident_operations.commit_lineage_deletion(token)

    @staticmethod
    def project_lineage_deletion(step: dict[str, Any]) -> None:
        lineage_resident_operations.project_lineage_deletion(step)

    @staticmethod
    def unbind_deleted_lineage_folder(step: dict[str, Any]) -> None:
        lineage_resident_operations.unbind_deleted_lineage_folder(step)

    @staticmethod
    def lineage_access_projection(lineage_id: str) -> dict[str, Any]:
        return lineage_domain.lineage_access_projection(lineage_id)

    @staticmethod
    def commit_resident_title_projection(resident_id: str, title: str, actor: str) -> dict[str, Any]:
        return lineage_domain.commit_resident_title_projection(resident_id, title, actor)

    @staticmethod
    def initialize_resident_lineage(record: dict[str, Any], actor: str) -> dict[str, Any]:
        return lineage_domain.initialize_resident_lineage(record, actor)

    @staticmethod
    def add_registered_resident_node(record: dict[str, Any], actor: str, *, node_options: dict[str, Any] | None=None) -> dict[str, Any] | None:
        return lineage_domain.add_registered_resident_node(record, actor, node_options=node_options)

    @staticmethod
    def move_resident_membership(
        source_lineage_id: str, record: dict[str, Any], actor: str, *, allow_empty_source: bool = False,
        publish_identity: Callable[[], None],
    ) -> None:
        lineage_domain.move_resident_membership(
            source_lineage_id, record, actor, allow_empty_source=allow_empty_source,
            publish_identity=publish_identity,
        )

    @staticmethod
    def finalize_deleted_resident_detach(lineage_id: str) -> None:
        lineage_resident_operations.finalize_deleted_resident_detach(lineage_id)

    @staticmethod
    def handle_deterministic_resident_message(lineage_id: str, resident_id: str, message: str, actor: str) -> dict[str, Any]:
        return lineage_domain.handle_deterministic_resident_message(lineage_id, resident_id, message, actor)

    @staticmethod
    def invalidate_lineage_read_cache(lineage_id: str) -> None:
        return lineage_domain.invalidate_lineage_read_cache(lineage_id)

    @staticmethod
    def actor_lineage_access_records(actor: str, lineage_ids: list[str] | None=None) -> list[dict[str, Any]]:
        return lineage_domain.actor_lineage_access_records(actor, lineage_ids)

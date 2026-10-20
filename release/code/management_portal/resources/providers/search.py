from __future__ import annotations

from typing import TYPE_CHECKING

from .common import canonical_record, canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


def install_resolver(service: "ResourceService") -> None:
    from management_portal import resource_identity_backend, search_identity_backend

    service.register_resolver(
        "search",
        lambda address, **context: canonical_resolution(
            search_identity_backend.resolve_search_address(
                address,
                actor=str(context.get("actor") or ""),
            )
        ),
        read_content=resource_identity_backend.read_resolved_resource_content,
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import search_identity_backend, semantic_search_jobs

    def unregister(resource_id, *, actor="", **_context):
        resolved = search_identity_backend.resolve_search_address(resource_id, actor=actor)
        record = resolved["record"]
        if resolved["exists"]:
            semantic_search_jobs.delete_search(record["surface"], {
                "search_id": resource_id, "project": record.get("project", ""),
            }, actor)
        return canonical_record(resource_id, {**record, "deleted": True, "status": "deleted"})

    service.register_mutation_provider("search", unregister=unregister)

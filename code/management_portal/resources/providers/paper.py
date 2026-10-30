from __future__ import annotations

from typing import TYPE_CHECKING

from .common import canonical_record, canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


def _read_content(resolved, **context):
    """Expose the Paper package's primary PDF as root resource content."""

    from management_portal import paper_resources, resource_identity_backend

    target = resolved
    if str(resolved.get("target_kind") or "") == "paper":
        root_id = str(resolved.get("root_id") or "").strip()
        target = canonical_resolution(
            paper_resources.resolve_paper_address(
                f"{root_id}/file/paper.pdf"
            )
        )
    return resource_identity_backend.read_resolved_resource_content(
        target,
        **context,
    )


def install_resolver(service: "ResourceService") -> None:
    from management_portal import paper_resources

    service.register_resolver(
        "paper",
        lambda address, **_context: canonical_resolution(
            paper_resources.resolve_paper_address(address)
        ),
        read_content=_read_content,
    )


def install_mutation_provider(service: "ResourceService") -> None:
    from management_portal import paper_resources, paper_store

    def unregister(resource_id, *, actor="", **_context):
        if paper_store._paper_by_id(resource_id) is not None:
            try:
                paper_resources.delete_paper({"paper_id": resource_id}, actor)
            except KeyError:
                if paper_store._paper_by_id(resource_id) is not None:
                    raise
        if paper_store._paper_by_id(resource_id) is not None:
            raise RuntimeError(f"{resource_id}: 删除尚未完成")
        return canonical_record(resource_id, {"deleted": True})

    service.register_mutation_provider("paper", unregister=unregister)

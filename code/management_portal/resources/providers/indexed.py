from __future__ import annotations

from typing import TYPE_CHECKING

from .common import canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


def _read_indexed_content(resolved, **context):
    from management_portal import resource_identity_backend

    return resource_identity_backend.read_resolved_resource_content(
        resolved,
        **context,
    )


def install_resolver(service: "ResourceService") -> None:
    from management_portal import resource_identity_backend

    service.register_index_backed_resolver(
        lambda address, **context: canonical_resolution(
            resource_identity_backend.resolve_indexed_resource_address(
                address,
                actor=str(context.get("actor") or ""),
                resource_service=service,
            )
        ),
        read_content=_read_indexed_content,
    )

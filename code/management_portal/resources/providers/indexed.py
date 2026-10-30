from __future__ import annotations

from typing import TYPE_CHECKING

from .common import canonical_resolution

if TYPE_CHECKING:
    from ..service import ResourceService


def _read_indexed_content(resolved, **context):
    from .. import indexed_content

    return indexed_content.read_resolved_resource_content(
        resolved,
        **context,
    )


def install_resolver(service: "ResourceService") -> None:
    from .. import indexed_content

    service.register_index_backed_resolver(
        lambda address, **context: canonical_resolution(
            indexed_content.resolve_indexed_resource_address(
                address,
                actor=str(context.get("actor") or ""),
                resource_service=service,
            )
        ),
        read_content=_read_indexed_content,
    )

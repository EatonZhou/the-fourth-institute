from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import canonical_record, canonical_resolution
from ..errors import ResourceVersionConflict

if TYPE_CHECKING:
    from ..service import ResourceService


from .ownership import KNOWLEDGE_TYPES as RESOURCE_TYPES

MUTABLE_RESOURCE_TYPES = tuple(
    resource_type
    for resource_type in RESOURCE_TYPES
    if resource_type != "experiment"
)
MARKDOWN_REGISTERABLE_RESOURCE_TYPES = tuple(
    resource_type
    for resource_type in MUTABLE_RESOURCE_TYPES
    if resource_type != "skill"
)


def install_resolver(service: "ResourceService", *, resource_types=None) -> None:
    import knowledge_board_lan.knowledge_backend as knowledge_backend
    from management_portal import resource_identity_backend

    service.register_resolver(
        tuple(resource_types) if resource_types is not None else MUTABLE_RESOURCE_TYPES,
        lambda address, **context: canonical_resolution(
            knowledge_backend.resolve_knowledge_address(
                address,
                actor=str(context.get("actor") or ""),
            )
        ),
        read_content=resource_identity_backend.read_resolved_resource_content,
    )


def _update_knowledge(
    resource_id: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    import knowledge_board_lan.knowledge_backend as knowledge_backend

    unknown = set(payload) - {"address", "text", "expected_version", "version_recovery_change_ids"}
    if unknown:
        raise ValueError(
            "unsupported Knowledge update fields: "
            + ", ".join(sorted(unknown))
        )
    if "text" not in payload:
        raise ValueError("Knowledge update requires text")
    address = str(payload.get("address") or resource_id).strip().strip("/")
    if address.partition("/")[0] != resource_id:
        raise ValueError("Knowledge update address must belong to resource_id")
    update_options: dict[str, Any] = {}
    if "expected_version" in payload:
        update_options["expected_version"] = payload.get("expected_version")
    if "version_recovery_change_ids" in payload:
        if payload["version_recovery_change_ids"] is None:
            raise ValueError("version_recovery_change_ids cannot be null")
        update_options["version_recovery_change_ids"] = payload["version_recovery_change_ids"]
    try:
        result = knowledge_backend.update_item_text(
            address,
            str(payload.get("text") or ""),
            actor=actor,
            **update_options,
        )
    except knowledge_backend.KnowledgeVersionConflict as exc:
        raise ResourceVersionConflict(
            str(exc),
            expected_version=exc.expected_version,
            current_version=exc.current_version,
        ) from exc
    return canonical_record(resource_id, result)


def _register_knowledge(
    service: "ResourceService",
    resource_type: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
    resource_id: str = "",
) -> dict[str, Any]:
    import knowledge_board_lan.knowledge_backend as knowledge_backend

    service.validate_id(resource_id, resource_type)
    return canonical_record(
        resource_id,
        knowledge_backend.register_item(
            resource_type,
            resource_id,
            payload,
            actor=actor,
        ),
    )


def _unregister_knowledge(
    resource_id: str,
    *,
    actor: str = "",
) -> dict[str, Any]:
    import knowledge_board_lan.knowledge_backend as knowledge_backend

    return canonical_record(
        resource_id,
        knowledge_backend.delete_item(resource_id, actor=actor),
    )


def install_mutation_provider(service: "ResourceService") -> None:
    for resource_type in MUTABLE_RESOURCE_TYPES:
        register = None
        if resource_type in MARKDOWN_REGISTERABLE_RESOURCE_TYPES:
            register = (
                lambda payload, _resource_type=resource_type, **context: (
                    _register_knowledge(
                        service,
                        _resource_type,
                        payload,
                        actor=str(context.get("actor") or ""),
                        resource_id=str(context.get("resource_id") or ""),
                    )
                )
            )
        service.register_mutation_provider(
            resource_type,
            register=register,
            update=lambda resource_id, payload, **context: _update_knowledge(
                resource_id,
                payload,
                actor=str(context.get("actor") or ""),
            ),
            content_updatable=True,
            unregister=lambda resource_id, **context: _unregister_knowledge(
                resource_id,
                actor=str(context.get("actor") or ""),
            ),
            service_managed_registration=True,
        )

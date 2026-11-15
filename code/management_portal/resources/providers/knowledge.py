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
    from .. import indexed_content

    service.register_resolver(
        tuple(resource_types) if resource_types is not None else MUTABLE_RESOURCE_TYPES,
        lambda address, **context: canonical_resolution(
            knowledge_backend.resolve_knowledge_address(
                address,
                actor=str(context.get("actor") or ""),
            )
        ),
        read_content=indexed_content.read_resolved_resource_content,
    )


def _update_knowledge(
    resource_id: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    import knowledge_board_lan.knowledge_backend as knowledge_backend

    unknown = set(payload) - {"address", "text", "body", "expected_version", "version_recovery_change_ids"}
    if unknown:
        raise ValueError(
            "unsupported Knowledge update fields: "
            + ", ".join(sorted(unknown))
        )
    if ("text" in payload) == ("body" in payload):
        raise ValueError("Knowledge update requires exactly one of text or body")
    address = str(payload.get("address") or resource_id).strip().strip("/")
    if address.partition("/")[0] != resource_id:
        raise ValueError("Knowledge update address must belong to resource_id")
    update_options: dict[str, Any] = {}
    if "body" in payload:
        if not isinstance(payload["body"], str):
            raise ValueError("Knowledge body must be a string")
        update_options["body"] = payload["body"]
    if "expected_version" in payload:
        update_options["expected_version"] = payload.get("expected_version")
    if "version_recovery_change_ids" in payload:
        if payload["version_recovery_change_ids"] is None:
            raise ValueError("version_recovery_change_ids cannot be null")
        update_options["version_recovery_change_ids"] = payload["version_recovery_change_ids"]
    try:
        result = knowledge_backend.update_item_text(
            address,
            str(payload.get("text") or "") if "text" in payload else None,
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


def install_action_provider(service: "ResourceService") -> None:
    def create_file(resource_id: str, payload: dict[str, Any], *, actor: str = "") -> dict[str, Any]:
        from knowledge_board_lan.knowledge_backend import create_item_file, KnowledgeVersionConflict
        from management_portal import permission_system
        from ..locking import mutation_projection_scope
        from ..content_changes import capture_content_changes

        if resource_id not in permission_system.permitted_ids(
            actor, "RunResourceAction", "resource_id", [resource_id],
        ):
            raise PermissionError("Knowledge file creation requires contributor permission")
        with mutation_projection_scope(resource_id):
            with capture_content_changes(actor=actor, resource_id=resource_id) as captured:
                try:
                    result = create_item_file(resource_id, actor=actor, **payload)
                except KnowledgeVersionConflict as exc:
                    raise ResourceVersionConflict(str(exc), expected_version=exc.expected_version,
                                                  current_version=exc.current_version) from exc
            change_recording = None
            if captured and (captured["errors"] or captured.get("unconfirmed_reason")):
                change_recording = {"status": "unconfirmed",
                                    "reason": captured.get("unconfirmed_reason") or "receipt_failed"}
            return service._complete_mutation(
                canonical_record(resource_id, result), resource_type=resource_id.partition("_")[0],
                expected_id=resource_id, actor=actor, kind="update",
                wait_for_index=True, index_timeout=45.0, change_recording=change_recording,
            )

    service.register_action_provider(MARKDOWN_REGISTERABLE_RESOURCE_TYPES, {
        "knowledge.file.create": create_file,
    })

"""ResourceService changes: registration, updates, unregistration and actions, and what
follows a committed change (identity checks, permission sync, index projection)."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .action import action_payload
from .mutation import mutation_payload

if TYPE_CHECKING:
    from .service import ResourceService


LOGGER = logging.getLogger(__name__)


def mutation_record(
    result: Mapping[str, Any],
    *,
    resource_type: str,
    expected_id: str = "",
) -> tuple[dict[str, Any], str]:
    if not isinstance(result, Mapping):
        raise TypeError(
            f"{resource_type} mutation provider must return an object"
        )
    record = dict(result)
    resource_id = str(record.get("resource_id") or "").strip()
    if not resource_id:
        raise ValueError(
            f"{resource_type} mutation result requires resource_id"
        )
    identity_aliases = sorted(
        key
        for key, value in record.items()
        if key != "resource_id"
        and (key == "id" or key.endswith("_id"))
        and str(value or "").strip() == resource_id
    )
    if identity_aliases:
        raise ValueError(
            "resource mutation result contains identity aliases: "
            + ", ".join(identity_aliases)
        )
    if expected_id and resource_id != expected_id:
        raise ValueError(
            f"resource mutation returned {resource_id!r}, expected {expected_id!r}"
        )
    return record, resource_id


def complete_mutation(
    service: "ResourceService",
    result: Mapping[str, Any],
    *,
    resource_type: str,
    expected_id: str,
    actor: str,
    kind: str,
    wait_for_index: bool,
    index_timeout: float,
    change_recording: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from management_common.operation_errors import failure_payload
    from .errors import ResourceOperationError

    resource_id = expected_id
    stage = "mutation_result"
    try:
        record, resource_id = service._mutation_record(
            result, resource_type=resource_type, expected_id=expected_id,
        )
        service._identity.validate(resource_id, resource_type)
        if change_recording:
            record["change_recording"] = change_recording
        if kind == "delete":
            stage = "registration_cleanup"
            service._registrations.forget(resource_id)
        stage = "authorization_sync"
        from management_portal import permission_system

        # Stores writing PostgreSQL records (Projects, Lineage objects, chatrooms, posts) place the object in
        # their own transaction; the file-backed ones (knowledge, papers, repos …) have no transaction to share
        # and are placed here, after their files are written.
        if kind == "delete":
            permission_system.retire_resources([resource_id])
        else:
            permission_system.sync_resource({**record, "resource_id": resource_id}, resource_type=resource_type)
        if kind == "create":
            stage = "index_projection"
            service._index().project_registered(resource_type, record)
            projection = {}
        else:
            if kind == "delete":
                stage = "index_projection"
                service._index().project_unregistered(resource_type, resource_id)
            stage = "index_notification"
            projection = service._notify_mutation(
                resource_id=resource_id,
                resource_type=resource_type,
                kind={"update": "updated", "delete": "deleted"}[kind],
                wait_for_index=wait_for_index,
                index_timeout=index_timeout,
            )
        if projection:
            record["index_projection"] = projection
        return record
    except Exception as exc:
        raise ResourceOperationError(
            "resource.mutation_post_commit_failed", str(exc), 500,
            details={
                "domain_committed": True,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "mutation": kind,
                "stage": stage,
                "cause": failure_payload(exc),
            },
        ) from exc


def notify_mutation(
    service: "ResourceService",
    *,
    path: str = "",
    resource_id: str,
    resource_type: str,
    kind: str,
    wait_for_index: bool = False,
    index_timeout: float = 45.0,
) -> dict[str, Any]:
    try:
        notify_options: dict[str, Any] = {}
        if path:
            notify_options["path"] = path
        if wait_for_index:
            notify_options.update({
                "wait": True,
                "timeout": index_timeout,
            })
        notification = service.notify_source_changed(
            resource_id=resource_id,
            resource_type=resource_type,
            kind=kind,
            **notify_options,
        )
        if wait_for_index and (
            not notification.get("targets")
            or notification.get("finished") is not True
        ):
            LOGGER.warning(
                "resource mutation index projection unconfirmed: %s",
                resource_id,
            )
            return {
                "status": "unconfirmed",
                "reason": (
                    "refresh_incomplete"
                    if notification.get("targets")
                    else "no_targets"
                ),
                "wait_requested": True,
                "targets": list(notification.get("targets") or []),
            }
    except Exception:
        # The domain registry write is authoritative. Reconciliation can
        # rebuild the derived index if this best-effort hint cannot queue.
        LOGGER.exception(
            "resource mutation committed but index notification failed: %s",
            resource_id,
        )
        return {
            "status": "unconfirmed",
            "reason": "notification_failed",
            "wait_requested": bool(wait_for_index),
            "targets": [],
        }
    return {}


def register(
    service: "ResourceService",
    resource_type: str,
    payload: Mapping[str, Any],
    *,
    actor: str = "",
    automatic: bool = False,
    wait_for_index: bool = False,
    index_timeout: float = 45.0,
) -> dict[str, Any]:
    clean_type = str(resource_type or "").strip()
    if not service._identity.is_registered(clean_type):
        raise ValueError(f"unregistered resource type: {clean_type}")
    clean_payload = mutation_payload(payload)
    service.resource_types.require(clean_type, registration=True)
    if "registration_key" in clean_payload:
        raise ValueError(
            "registration_key is internal to ResourceService; "
            "submit only resource fields"
        )
    service._ensure_mutation_providers(clean_type)
    provider = service._mutation_providers.provider_for(clean_type)
    clean_actor = str(actor or "").strip()
    if not automatic:
        service._require_mutation_actor(actor)
    if provider.register is None:
        raise ValueError(f"{clean_type} resources do not support registration")
    expected_id = ""
    provider_context: dict[str, Any] = {
        "actor": clean_actor,
        "automatic": bool(automatic),
    }
    if provider.service_managed_registration:
        expected_id, created_reservation = service._registrations.reserve(
            clean_type,
            clean_payload,
            actor=clean_actor,
            allocate=lambda: service.new_id(clean_type),
        )
        provider_context["resource_id"] = expected_id
        provider_context["reservation_created"] = created_reservation
    result = provider.register(clean_payload, **provider_context)
    return service._complete_mutation(
        result, resource_type=clean_type, expected_id=expected_id,
        actor=clean_actor, kind="create", wait_for_index=wait_for_index,
        index_timeout=index_timeout,
    )


def existing_identity(
    service: "ResourceService",
    resource_id: str,
    resource_type: str = "",
    *,
    actor: str = "",
):
    clean_id = str(resource_id or "").strip()
    clean_type = str(resource_type or "").strip()
    if clean_type:
        return service._identity.validate(clean_id, clean_type)
    try:
        return service._identity.parse(clean_id)
    except ValueError as parse_error:
        try:
            record = service.get(clean_id, actor=actor)
        except (KeyError, RuntimeError):
            raise parse_error
        return service._identity.validate(
            clean_id,
            str(record.get("resource_type") or ""),
        )


def require_mutation_actor(actor: str) -> None:
    from management_common.principal import is_user_or_resident

    if not is_user_or_resident(str(actor or "").strip()):
        raise PermissionError("Resource mutation requires an authenticated User or Resident")


def update(
    service: "ResourceService",
    resource_id: str,
    payload: Mapping[str, Any],
    *,
    actor: str = "",
    resource_type: str = "",
    wait_for_index: bool = False,
    index_timeout: float = 45.0,
) -> dict[str, Any]:
    identity = service._existing_identity(resource_id, resource_type, actor=actor)
    service._ensure_mutation_providers(identity.resource_type)
    provider = service._mutation_providers.provider_for(identity.resource_type)
    service._require_mutation_actor(actor)
    clean_payload = mutation_payload(payload)
    if provider.update is None:
        raise ValueError(
            f"{identity.resource_type} resources do not support updates"
        )
    from .content_changes import capture_content_changes

    with capture_content_changes(actor=actor, resource_id=identity.resource_id) as captured:
        result = provider.update(
            identity.resource_id,
            clean_payload,
            actor=str(actor or ""),
        )
    change_recording = None
    if captured and (captured["errors"] or captured.get("unconfirmed_reason")):
        change_recording = {
            "status": "unconfirmed", "reason": captured.get("unconfirmed_reason") or "receipt_failed",
        }
    return service._complete_mutation(
        result, resource_type=identity.resource_type, expected_id=identity.resource_id,
        actor=actor, kind="update", wait_for_index=wait_for_index,
        index_timeout=index_timeout, change_recording=change_recording,
    )


def execution_content_changes(service: "ResourceService", execution_id: str, *, actor: str) -> list[dict[str, Any]]:
    from management_common.principal import is_user_or_resident
    from .content_changes import ContentChangeStore

    if not execution_id or not is_user_or_resident(actor):
        raise PermissionError("Execution content changes require their authenticated author")
    changes = ContentChangeStore().for_execution(execution_id, actor)
    # Possession of an execution id never grants access to another author's receipts.
    return changes


def unregister(
    service: "ResourceService",
    resource_id: str,
    *,
    actor: str = "",
    resource_type: str = "",
    wait_for_index: bool = False,
    index_timeout: float = 45.0,
) -> dict[str, Any]:
    identity = service._existing_identity(resource_id, resource_type, actor=actor)
    service._ensure_mutation_providers(identity.resource_type)
    provider = service._mutation_providers.provider_for(identity.resource_type)
    service._require_mutation_actor(actor)
    if provider.unregister is None:
        raise ValueError(
            f"{identity.resource_type} resources do not support unregistration"
        )
    result = provider.unregister(
        identity.resource_id,
        actor=str(actor or ""),
    )
    return service._complete_mutation(
        result, resource_type=identity.resource_type, expected_id=identity.resource_id,
        actor=actor, kind="delete", wait_for_index=wait_for_index,
        index_timeout=index_timeout,
    )


def action(
    service: "ResourceService",
    resource_id: str,
    action: str,
    payload: Mapping[str, Any] | None = None,
    *,
    actor: str = "",
    resource_type: str = "",
) -> dict[str, Any]:
    identity = service._existing_identity(resource_id, resource_type, actor=actor)
    clean_action = str(action or "").strip()
    service._ensure_action_providers(identity.resource_type)
    handler = service._action_providers.handler_for(identity.resource_type, clean_action)
    service._require_mutation_actor(actor)
    clean_payload = action_payload(payload)
    result = handler(
        identity.resource_id,
        clean_payload,
        actor=str(actor or ""),
    )
    if not isinstance(result, Mapping):
        raise TypeError(
            f"{identity.resource_type} action {clean_action} must return an object"
        )
    return dict(result)

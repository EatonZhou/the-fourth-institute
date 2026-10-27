"""Resident domain responsibilities: lifecycle."""
from __future__ import annotations

from . import resident_backend, resident_catalog, resident_conversation_stream, resident_record_domain
from typing import Any, Mapping
import copy



def resident_aggregate_lineage_id(resident_id: object) -> str:
    """Resolve graph membership for operations that also change topology."""

    clean_id = str(resident_id or "").strip()
    if not clean_id:
        raise ValueError("resident_id is required for Resident mutation locking")
    try:
        return resident_catalog.get_catalog().target(clean_id).lineage_id
    except KeyError as exc:
        raise KeyError(
            f"Resident mutation locking requires a registered Resident: {clean_id}"
        ) from exc


def resident_has_pending_work(resident_id: object) -> bool:
    if resident_record_domain.lifecycle_operations().pending(str(resident_id or "")):
        return True
    state = resident_conversation_stream.get_repository().current_state(str(resident_id or ""))
    return str(state.get("work_state") or "idle") in {
        "queued",
        "running",
        "stopping",
    }


def resident_turn_running(resident_id: str) -> bool:
    state = resident_conversation_stream.get_repository().current_state(resident_id)
    return str(state.get("work_state") or "idle") in {"running", "stopping"}


def ready_resident_lifecycle_operations() -> list[str]:
    return resident_record_domain.lifecycle_operations().ready()


def resident_lifecycle_health() -> dict[str, Any]:
    return resident_record_domain.lifecycle_operations().health()


def observe_committed_resident_transfer(operation_id: str, resident_id: str) -> dict[str, Any]:
    """Internal Lineage port: caller has verified receipt and both read grants."""
    return resident_record_domain.lifecycle_operations().observe_committed_transfer(operation_id, resident_id)


def resident_release_step(record: dict[str, Any], actor: str) -> dict[str, Any]:
    return {
        "kind": "release", "record": {
            key: copy.deepcopy(record[key]) for key in (
                "resident_id", "lineage_id", "backend", "backend_route_id",
                "workspace_path", "owned_workspace_id", "owned_workspace", "default_cwd",
            ) if key in record
        }, "actor": actor,
        "binding": resident_backend.session_record(record),
    }


def append_conversation_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Persist one complete message through the conversation stream authority."""
    from management_common.principal import message_origin

    message = copy.deepcopy(message)
    if message.get("role") == "agent":
        from management_common.async_questions import decode_question_content

        message["content_parts"] = decode_question_content(
            message.get("content_parts") or [], resident_id, message["message_id"],
        )
    if "origin" not in message and message.get("role") in {"agent", "system"}:
        sender = resident_id if message["role"] == "agent" else "portal"
        message["origin"] = message_origin(sender)
        message["author_id"] = sender
    return resident_conversation_stream.get_repository().append_message(
        resident_id,
        message,
    )


def mark_resident_input_queued(
    resident_id: str,
    message: Mapping[str, Any],
) -> None:
    resident_conversation_stream.get_repository().set_input_state(
        resident_id,
        str(message.get("message_id") or ""),
        "queued",
    )


def resident_user_message_count(resident_id: object) -> int:
    key = str(resident_id or "").strip()
    if not key:
        return 0
    return resident_conversation_stream.get_repository().user_message_counts([key]).get(key, 0)


def persist_resident_user_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return resident_conversation_stream.get_repository().commit_user_message(
        resident_id,
        message,
    )


def accept_resident_user_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return resident_conversation_stream.get_repository().accept_user_message(
        resident_id,
        message,
    )

"""The work of a Lineage node's Resident: delivering messages, intake, cancelling, its queue and quiescing."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import LineageConflictError
from .lineage_values import _clone_json, _safe_actor, _text
from .lineage_view_projection import node_resident_id as _node_resident_id
from . import lineage_primitives, resident_protocol
from . import lineage_aggregate, resident_message_contract
from . import lineage_state


def _send_node_message(
    node: dict[str, Any],
    prompt: str,
    actor: str,
    *,
    input_media: object = None,
    message_id: str = "",
    instruction_context: Any = None,
) -> object:
    """Lower a Resident Invoke to the same ISA call used by an explicit Effect."""
    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionContext

    prompt = lineage_primitives.normalize_invocation_prompt(prompt)
    content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if isinstance(input_media, list):
        content_parts.extend(
            dict(item) for item in input_media if isinstance(item, Mapping)
        )
    delivery_message_id = resident_message_contract.message_identity(message_id)
    return routing.execute_or_raise(
        {
            "instruction": "DeliverMessage",
            "idempotency_key": f"lineage-delivery:{delivery_message_id}",
            "arguments": {
                "resident_id": _node_resident_id(node),
                "message": {
                    "message_id": delivery_message_id,
                    "role": "user",
                    "content_parts": content_parts,
                },
                "activation_policy": "enqueue_and_trigger",
            },
        },
        instruction_context or InstructionContext(caller=actor, entry="lineage"),
    )


def change_lineage_resident_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    """Open or close the Resident intake switch through its owner."""

    actor_id = _safe_actor(actor)
    resident_id = _text(
        payload.get("resident_id"),
        80,
    ).lower()
    state_domain = _text(
        payload.get("state_domain"),
        40,
    ).lower() or "intake"
    transition = _text(payload.get("transition"), 24).lower()
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_aggregate.ensure_manager(lineage, actor_id)
    node = _clone_json(lineage_state._lineage_node(lineage, resident_id))
    if state_domain == "intake":
        return resident_protocol.change_resident_state(
            _node_resident_id(node),
            actor_id,
            state_domain=state_domain,
            transition=transition,
            allow_quota_override=allow_quota_override,
        )
    raise ValueError("未知的 Resident 状态域")


def cancel_lineage_resident_execution(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Stop one message: a running turn is cancelled, a waiting one leaves the
    queue, and a finished one keeps its result (decision_2a3ff353165c)."""

    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    message_id = _text(payload.get("message_id"), 100)
    # Stop only needs identity, membership and authority. Loading the graph's
    # component bodies and runtime work here delays cancellation as it grows.
    if lineage_aggregate.lineage_deletion_intent(lineage_id):
        raise LineageConflictError("Lineage deletion is in progress")
    lineage = lineage_state.lineage_access_projection(lineage_id)
    lineage_aggregate.ensure_manager(lineage, actor_id)
    if resident_id not in lineage.get("lineage_node_ids", ()):
        raise ValueError("Lineage 中不存在对应的 Agent 节点")
    return resident_protocol.cancel_resident_input(
        resident_id, actor_id, message_id,
    )


def _lineage_resident_queue_target(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> tuple[str, str, str]:
    """Resolve one HATG node to the Resident queue authority locator."""

    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    queue_item_id = str(payload.get("message_id") or "").strip()
    if not resident_id or not queue_item_id:
        raise ValueError(
            "Resident queue Instruction 需要 resident_id 和 message_id"
        )
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_aggregate.ensure_manager(lineage, actor_id)
    node = _clone_json(lineage_state._lineage_node(lineage, resident_id))
    return (
        actor_id,
        _node_resident_id(node),
        queue_item_id,
    )


def inject_lineage_resident_queue_item_into_active_turn(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    actor_id, resident_id, message_id = _lineage_resident_queue_target(
        lineage_id, payload, actor,
    )
    return resident_protocol.inject_queued_input_into_active_turn(
        resident_id, actor_id, message_id,
    )


def clear_lineage_resident_queue(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    resident_id = _text(payload.get("resident_id"), 80).lower()
    if not resident_id:
        raise ValueError("resident_id required")
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_aggregate.ensure_manager(lineage, actor_id)
    node = lineage_state._lineage_node(lineage, resident_id)
    return resident_protocol.clear_resident_queue(_node_resident_id(node), actor_id)


def cancel_lineage_resident_queue_item(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Cancel one queued Resident turn without changing the active turn."""

    (
        actor_id,
        resident_id,
        queue_item_id,
    ) = _lineage_resident_queue_target(lineage_id, payload, actor)
    result = resident_protocol.cancel_resident_queue_item(
        resident_id,
        actor_id,
        queue_item_id,
    )
    return {
        **result,
        "resident_id": resident_id,
        "message_id": queue_item_id,
    }


def quiesce_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Close intake, cancel current work and discard that session's queue."""

    actor_id = _safe_actor(actor)
    resident_id = _text(
        payload.get("target"),
        80,
    ).lower()
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(lineage, actor_id)
        node = _clone_json(lineage_state._lineage_node(lineage, resident_id))
    exact_resident_id = _node_resident_id(node)
    intake = resident_protocol.change_resident_state(
        exact_resident_id,
        actor_id,
        state_domain="intake",
        transition="close",
    )
    resident_status = resident_protocol.get_resident_state(
        exact_resident_id,
        actor_id,
    )["state"]
    active_message_id = str(
        resident_status.get("active_message_id") or ""
    )
    operation = (
        resident_protocol.cancel_resident_execution(
            exact_resident_id,
            actor_id,
            active_message_id,
            continue_queue=False,
        )
        if active_message_id
        else {
            "cancelled": False,
            **resident_protocol.clear_resident_queue(exact_resident_id, actor_id),
        }
    )
    return {
        "target_resident_id": resident_id,
        "intake_state": str(intake.get("state") or "closed"),
        "cleared_queue_count": int(
            operation.get("cleared_queue_count") or operation.get("cleared_count") or 0
        ),
        "cancelled": bool(operation.get("cancelled")),
        "state": dict(operation.get("state") or {}),
        "cursor": int(operation.get("cursor") or 0),
    }

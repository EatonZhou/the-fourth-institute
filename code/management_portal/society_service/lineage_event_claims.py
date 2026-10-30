"""Claim Event subscribers and queued deliveries under the caller's Lineage lock."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import (
    lineage_event_delivery,
    lineage_operator_runtime,
    lineage_primitives,
    lineage_program_vocabulary,
    lineage_program_wire_queue,
)


@dataclass(frozen=True)
class GenericDeliveryClaims:
    routes: list[dict[str, Any]]
    dispatch_tokens: dict[str, str]
    blocked_results: list[dict[str, Any]] = field(default_factory=list)


def claim_generic_deliveries(
    lineage_id: str,
    event_source_id: str,
    cursor: str,
    event: dict[str, Any],
    available_routes: list[dict[str, Any]],
    preclaimed_wire_ids: set[str],
) -> GenericDeliveryClaims:
    """Stage each available wire, then claim only this Event occurrence."""
    routes_by_id = {
        str(route["wire_id"]): route for route in available_routes
    }
    dispatch_tokens: dict[str, str] = {}
    blocked_results: list[dict[str, Any]] = []
    claimed_ids = {
        wire_id for wire_id in routes_by_id if wire_id in preclaimed_wire_ids
    }
    for wire_id in routes_by_id:
        if wire_id in claimed_ids:
            continue
        staged = lineage_program_wire_queue.stage_event_delivery(
            lineage_id,
            event_source_id,
            wire_id,
            cursor,
            event,
            subscriber_id=wire_id,
        )
        if staged.get("status") == "delivered":
            continue
        parallel = bool((routes_by_id[wire_id].get("config") or {}).get("parallel"))
        pending = lineage_program_wire_queue.claim_pending_event(
            lineage_id, wire_id, cursor=cursor if parallel else "",
            event_source_id=event_source_id,
            target_id=str(routes_by_id[wire_id].get("target_id") or ""),
        )
        if pending and pending.get("status") == "paused":
            blocked_results.append({
                **pending, "wire_id": wire_id,
                "target_id": str(routes_by_id[wire_id].get("target_id") or ""),
            })
            continue
        if (
            pending
            and str(pending.get("cursor") or "") == cursor
            and str(pending.get("event_source_id") or "") == event_source_id
        ):
            claimed_ids.add(wire_id)
            dispatch_tokens[wire_id] = str(pending.get("dispatch_token") or "")
    return GenericDeliveryClaims(
        routes=[
            routes_by_id[wire_id]
            for wire_id in routes_by_id
            if wire_id in claimed_ids
        ],
        dispatch_tokens=dispatch_tokens,
        blocked_results=blocked_results,
    )


def defer_program_event_route(
    lineage_id: str,
    event_source_id: str,
    route: dict[str, Any],
    event: lineage_primitives.LineageEvent,
    cursor: str,
    *,
    reason: str,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the wire's unavailable policy to one Event occurrence."""
    config = route.get("config") if isinstance(route.get("config"), dict) else {}
    wire_id = str(route.get("wire_id") or "")
    # Someone asked for a manual run on purpose: while the target is busy it
    # waits for its turn instead of being dropped without a trace.
    default_policy = (
        "queue" if event.type == "manual.triggered"
        else lineage_program_vocabulary.DEFAULT_EVENT_UNAVAILABLE_POLICY
    )
    result = lineage_program_wire_queue.defer_event_for_wire(
        lineage_id,
        event_source_id,
        wire_id,
        cursor,
        event.to_dict(),
        policy=str(config.get("unavailable_policy") or default_policy),
        queue_limit=int(
            config.get("queue_limit") or lineage_program_vocabulary.DEFAULT_EVENT_QUEUE_LIMIT
        ),
        expires_after_seconds=int(config.get("expires_after_seconds") or 0),
        subscriber_id=wire_id,
        invocation=invocation,
    )
    return {
        **result,
        "target_id": str(route.get("target_id") or ""),
        "target_kind": str(route.get("target_kind") or ""),
        "reason": reason,
        "event_id": event.id,
    }


def prepare_operator_inboxes(
    lineage_id: str,
    event: lineage_primitives.LineageEvent,
    barriers: list[dict[str, Any]],
) -> None:
    """Reserve Packet Operator inboxes, rolling back a partial preparation."""
    prepared_operator_ids: list[str] = []
    try:
        for barrier in barriers:
            lineage_operator_runtime.prepare_inbox(
                lineage_id,
                barrier["operator_id"],
                event=event,
                expected_wire_ids=barrier["expected_packet_wire_ids"],
            )
            prepared_operator_ids.append(str(barrier["operator_id"]))
    except Exception:
        for operator_id in prepared_operator_ids:
            lineage_operator_runtime.pop_inbox(
                lineage_id, operator_id, event.id
            )
        raise


def release_barrier_failure_claims(
    lineage_id: str,
    generic_claims: GenericDeliveryClaims,
    error: object,
    *,
    consumed: bool = False,
) -> None:
    """Settle staged deliveries when Operator barriers cannot be prepared.

    An explicitly cancelled Operator Event consumes them; any other failure
    releases them so the occurrence can retry.
    """
    diagnostic = str(error or "").strip()
    for wire_id, dispatch_token in generic_claims.dispatch_tokens.items():
        if dispatch_token:
            lineage_event_delivery.settle_delivery(
                lineage_id, wire_id, dispatch_token,
                {"wire_id": wire_id, "status": "cancelled" if consumed else "failed",
                 "error": diagnostic},
            )

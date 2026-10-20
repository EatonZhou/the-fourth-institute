"""Program Event dispatch policy; aggregate state and commit remain with the authority."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from management_common.limit_contract import normalize_optional_limit

from . import (
    lineage_event_claims,
    lineage_event_delivery,
    lineage_event_dispatch_rules,
    lineage_event_routing,
    lineage_instruction_context,
    lineage_operator_runtime,
    lineage_primitives,
    lineage_program,
    lineage_program_effect_execution,
    lineage_program_execution,
    lineage_program_identity,
    lineage_program_runtime,
)
from .lineage_program_contracts import _program_event_source_contract_fingerprint
from .lineage_program_queries import _compiled_program, _program_operator_barriers
from .lineage_program_runtime_projection import _program_invoke_wire_runtime_view
from .lineage_values import _clone_json, _safe_actor, _text


class ProgramEventAuthority(Protocol):
    """One aggregate state owner and one delivery boundary for Program policy."""

    def read_authorized(self, lineage_id: str, actor: str) -> dict[str, Any]: ...
    def ensure_manager(self, lineage: dict[str, Any], actor: str) -> None: ...
    def lineage_scope(self, lineage_id: str) -> AbstractContextManager[None]: ...
    def read_lineage_state(self, lineage_id: str) -> dict[str, Any]: ...
    def program_execution_ports(self) -> lineage_program_execution.ProgramExecutionPorts: ...
    def effect_authority(self) -> lineage_program_effect_execution.ProgramEffectAuthority: ...
    def node_has_active_execution(self, node: dict[str, Any], actor: str) -> bool: ...
    def send_node_message(self, node: dict[str, Any], prompt: str, actor: str, *, message_id: str, input_media: object = None, instruction_context: Any = None) -> dict[str, Any]: ...
    def register_program_agent_invocation(self, lineage_id: str, target_id: str, message_id: str, event: lineage_primitives.LineageEvent, source_wire_id: str, actor: str) -> dict[str, Any]: ...
    def dispatch_lineage_program_event(self, lineage_id: str, event_source_id: str, event: lineage_primitives.LineageEvent, actor: str, *, cursor: str, expected_event_source_contract: str) -> dict[str, Any]: ...


def _dispatch_program_foundational_event_route(
    lineage_id: str,
    actor: str,
    event_source_id: str,
    event: lineage_primitives.LineageEvent,
    route: dict[str, Any],
    lineage: dict[str, Any],
    plan: dict[str, Any],
    *, authority: ProgramEventAuthority,
) -> dict[str, Any]:
    """An Event source produces a packet; all consumers share Route delivery."""
    wire_id = str(route.get("wire_id") or "")
    source_id = plan["event_sources"][event_source_id]["component_id"]
    wire = next(wire for wire in plan["wires"] if wire["id"] == wire_id)
    wire = {**wire, "config": route.get("config", wire.get("config", {}))}
    result = lineage_program_execution.dispatch_program_packets(
        lineage, plan, source_id, event, [lineage_primitives.event_packet(event)],
        actor, packet_wires=[wire], invocation_arguments=route.get("argument_overrides"),
        ports=authority.program_execution_ports(),
    )
    outcomes = result["deliveries"] + result["operators"]
    return {"wire_id": wire_id, "target_id": route["target_id"],
            **(outcomes[0] if outcomes else {"status": "empty"})}


def _program_event_route_can_consume(
    lineage_id: str,
    lineage: dict[str, Any],
    route: dict[str, Any],
    actor: str,
    *, authority: ProgramEventAuthority,
) -> tuple[bool, str]:
    """Return whether the wire target can accept a new Event right now."""

    target_kind = str(route.get("target_kind") or "")
    if target_kind in {"effect", "loop"}:
        return True, ""
    target_id = str(route.get("target_id") or "")
    if target_kind in lineage_program.PACKET_OPERATOR_KINDS:
        try:
            runtime = lineage_operator_runtime.read_operator_runtime(
                lineage_id,
                target_id,
            )
        except (OSError, ValueError):
            return False, "packet_operator_runtime_unavailable"
        if any(not item.get("recovery_blocked") for item in (runtime.get("pending_deliveries") or {}).values()):
            return False, "packet_operator_working"
        return True, ""
    target = next(
        (
            node
            for node in lineage.get("nodes") or []
            if (
                isinstance(node, dict)
                and str(node.get("id") or "") == target_id
            )
        ),
        None,
    )
    if not target:
        return False, "target_missing"
    if authority.node_has_active_execution(target, actor):
        return False, "resident_running"
    return True, ""



def _dispatch_lineage_program_event_locked(
    lineage_id: str,
    event_source_id: str,
    event: lineage_primitives.LineageEvent | dict[str, Any],
    actor: str,
    *,
    cursor: str,
    expected_event_source_contract: str = "",
    event_source_override: dict[str, Any] | None = None,
    selected_wire_ids: set[str] | None = None,
    preclaimed_wire_ids: set[str] | None = None,
    bypass_unavailable_policy: bool = False,
    invocation_arguments: dict[str, dict[str, Any]] | None = None,
    authority: ProgramEventAuthority,
) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    normalized_event = lineage_primitives.normalize_event(event)
    from dataclasses import replace

    from management_common.principal import OperationIdentity

    if normalized_event.identity:
        identity = OperationIdentity.from_dict(normalized_event.identity)
        if identity.principal.id != actor_id:
            raise PermissionError("Event identity disagrees with its dispatch principal")
    else:
        normalized_event = replace(normalized_event, identity=OperationIdentity.for_actor(actor_id).to_dict())
    authorized = authority.read_authorized(lineage_id, actor_id)
    authority.ensure_manager(authorized, actor_id)
    with authority.lineage_scope(lineage_id):
        lineage = authority.read_lineage_state(lineage_id)
        plan = _compiled_program(lineage)
        event_source = (
            event_source_override
            if isinstance(event_source_override, dict)
            else (
                plan.get("event_sources", {}).get(event_source_id)
                if isinstance(plan.get("event_sources"), dict)
                else None
            )
        )
        if not event_source:
            if expected_event_source_contract:
                return {"stale_contract": True, "matched_count": 0, "results": []}
            raise KeyError("program Event Source not found")
        if (
            expected_event_source_contract
            and _program_event_source_contract_fingerprint(event_source)
            != expected_event_source_contract
        ):
            return {
                "event": normalized_event.to_dict(),
                "event_source_id": event_source_id,
                "matched_count": 0,
                "duplicate": False,
                "stale_contract": True,
                "results": [],
            }
        fire_claim = lineage_program_runtime.claim_event_occurrence(
            lineage_id,
            event_source_id,
            cursor,
            max_fires=normalize_optional_limit(
                event_source.get("max_fires")
            ),
        )
        if not fire_claim.get("accepted"):
            return {
                "event": normalized_event.to_dict(),
                "event_source_id": event_source_id,
                "matched_count": 0,
                "consumed_count": 0,
                "duplicate": False,
                "exhausted": bool(fire_claim.get("exhausted")),
                "paused": fire_claim.get("status") == "paused",
                "fire": fire_claim,
                "results": [],
            }
        preclaimed_wire_id_set = {
            str(item) for item in preclaimed_wire_ids or set() if str(item)
        }
        subscriber_routes = lineage_event_dispatch_rules.prepare_subscriber_routes(
            event_source,
            selected_wire_ids,
            invocation_arguments,
        )
        wire_runtime = (
            {}
            if bypass_unavailable_policy
            else _program_invoke_wire_runtime_view(lineage)
        )
        selection = lineage_event_routing.select_event_routes(
            lineage_id,
            event_source_id,
            subscriber_routes,
            normalized_event,
            cursor,
            wire_runtime,
            bypass_unavailable_policy=bypass_unavailable_policy,
            can_consume_target=lambda route: _program_event_route_can_consume(
                lineage_id, lineage, route, actor_id, authority=authority
            ),
        )
        available_routes = selection.available_routes
        budget_results = selection.unavailable_results
        handled_unavailable_count = selection.handled_unavailable_count
        duplicate_unavailable_count = selection.duplicate_unavailable_count
        generic_claims = lineage_event_claims.claim_generic_deliveries(
            lineage_id,
            event_source_id,
            cursor,
            normalized_event.to_dict(),
            available_routes,
            preclaimed_wire_id_set,
        )
        claimed_routes = generic_claims.routes
        local_dispatch_tokens = generic_claims.dispatch_tokens
        budget_results = [*budget_results, *generic_claims.blocked_results]
        if not claimed_routes:
            return {
                "event": normalized_event.to_dict(),
                "event_source_id": event_source_id,
                "matched_count": len(generic_claims.blocked_results),
                "consumed_count": handled_unavailable_count,
                "duplicate": bool(
                    duplicate_unavailable_count
                    or (available_routes and not claimed_routes and not generic_claims.blocked_results)
                ),
                "fire": fire_claim,
                "results": budget_results,
            }
        lineage_snapshot = _clone_json(lineage)

    barriers = _program_operator_barriers(
        lineage_snapshot,
        event_source_id,
        route_ids=[str(route.get("wire_id") or "") for route in claimed_routes],
    )
    try:
        lineage_event_claims.prepare_operator_inboxes(
            lineage_id, normalized_event, barriers
        )
    except Exception as exc:
        cancelled = isinstance(
            exc, lineage_operator_runtime.PacketOperatorEventCancelledError
        )
        lineage_event_claims.release_barrier_failure_claims(
            lineage_id, generic_claims, exc, consumed=cancelled,
        )
        if not cancelled:
            raise
        return {
            "event": normalized_event.to_dict(),
            "event_source_id": event_source_id,
            "matched_count": 0,
            "consumed_count": handled_unavailable_count,
            "duplicate": False,
            "fire": fire_claim,
            "results": [
                *budget_results,
                {
                    "status": "cancelled",
                    "event_id": normalized_event.id,
                    "reason": "packet_operator_barrier_cancelled",
                    "error": str(exc),
                },
            ],
        }

    results: list[dict[str, Any]] = list(budget_results)
    route_results, matched_count = lineage_event_delivery.dispatch_generic_routes(
        lineage_id,
        claimed_routes,
        local_dispatch_tokens,
        lambda route: _dispatch_program_foundational_event_route(
            lineage_id,
            actor_id,
            event_source_id,
            normalized_event,
            route,
            lineage_snapshot,
            plan,
            authority=authority,
        ),
    )
    results.extend(route_results)
    return {
        "event": normalized_event.to_dict(),
        "event_source_id": event_source_id,
        "matched_count": matched_count + len(generic_claims.blocked_results),
        "consumed_count": handled_unavailable_count,
        "duplicate": False,
        "fire": fire_claim,
        "results": results,
    }



MAX_PROGRAM_RUNTIME_EVENT_DEPTH = 24


def _dispatch_program_runtime_event_watchers(
    lineage_id: str,
    plan: dict[str, Any],
    actor: str,
    *,
    event_type: str,
    observed_event_source_id: str,
    original_event: lineage_primitives.LineageEvent,
    payload: dict[str, Any],
    wire_ids: Iterable[str] = (),
    cursor_suffix: str = "",
    authority: ProgramEventAuthority,
) -> list[dict[str, Any]]:
    """Route one operational fact through explicit Event Source watchers."""

    from management_common.principal import OperationIdentity
    if not original_event.identity:
        raise PermissionError("Runtime Event has no verified identity; explicit reissue is required")
    actor = OperationIdentity.from_dict(original_event.identity).principal.id

    try:
        runtime_depth = int(
            (original_event.payload or {}).get("runtime_event_depth") or 0
        )
    except (TypeError, ValueError):
        runtime_depth = 0
    if runtime_depth >= MAX_PROGRAM_RUNTIME_EVENT_DEPTH:
        return []
    observed_wire_ids = {
        str(wire_id)
        for wire_id in wire_ids
        if str(wire_id)
    }
    event_sources = (
        plan.get("event_sources")
        if isinstance(plan.get("event_sources"), dict)
        else {}
    )
    watchers: list[tuple[str, dict[str, Any], str]] = []
    for watcher_id, watcher in event_sources.items():
        if (
            not isinstance(watcher, dict)
            or watcher.get("kind") != "event_source"
            or str(watcher.get("event_type") or "") != event_type
        ):
            continue
        subject_source_id = str(
            watcher.get("subject_event_source_id") or ""
        )
        subject_wire_id = str(watcher.get("subject_wire_id") or "")
        if subject_source_id == observed_event_source_id:
            watchers.append((str(watcher_id), watcher, ""))
        elif subject_wire_id and subject_wire_id in observed_wire_ids:
            watchers.append((str(watcher_id), watcher, subject_wire_id))

    results: list[dict[str, Any]] = []
    for watcher_id, watcher, subject_wire_id in sorted(
        watchers,
        key=lambda item: item[0],
    ):
        runtime_payload = {
            "event_source_id": observed_event_source_id,
            "source_event_id": original_event.id,
            "runtime_event_depth": runtime_depth + 1,
            **payload,
            "trigger_path_id": lineage_program_identity.event_path_id(lineage_id, original_event),
        }
        if subject_wire_id:
            runtime_payload["wire_id"] = subject_wire_id
        runtime_event = lineage_primitives.make_event(
            event_type,
            payload=runtime_payload,
            identity=original_event.identity or None,
            correlation=(
                f"{original_event.id}:{event_type}:{watcher_id}:"
                f"{cursor_suffix}"
            ),
        )
        results.append(
            authority.dispatch_lineage_program_event(
                lineage_id,
                watcher_id,
                runtime_event,
                actor,
                cursor=(
                    f"event-runtime:{event_type}:{watcher_id}:"
                    f"{original_event.id}:{cursor_suffix}"
                ),
                expected_event_source_contract=(
                    _program_event_source_contract_fingerprint(watcher)
                ),
            )
        )
    return results



def dispatch_program_event(
    lineage_id: str,
    event_source_id: str,
    event: lineage_primitives.LineageEvent | dict[str, Any],
    actor: str,
    *,
    cursor: str,
    expected_event_source_contract: str = "",
    event_source_override: dict[str, Any] | None = None,
    selected_wire_ids: set[str] | None = None,
    preclaimed_wire_ids: set[str] | None = None,
    bypass_unavailable_policy: bool = False,
    invocation_arguments: dict[str, dict[str, Any]] | None = None,
    authority: ProgramEventAuthority,
) -> dict[str, Any]:
    """Serialize reservation, Packet Operator barriers, deliveries and Effects."""

    from dataclasses import replace

    from management_common.principal import OperationIdentity
    actor_id = _safe_actor(actor)
    normalized_input = lineage_primitives.normalize_event(event)
    if normalized_input.identity:
        if OperationIdentity.from_dict(normalized_input.identity).principal.id != actor_id:
            raise PermissionError("Event identity disagrees with its dispatch principal")
    else:
        normalized_input = replace(normalized_input, identity=OperationIdentity.for_actor(actor_id).to_dict())
    from . import trigger_paths
    cause = normalized_input.payload.get("cause") or {}
    cause_kind = str(cause.get("kind") or ("timer" if normalized_input.type == "time.reached" else "external"))
    cause_id = str(cause.get("id") or cursor or normalized_input.id)
    # Serialize admission with PauseComponent. Occurrence claiming checks again
    # before fanout; no external work runs inside this control transaction.
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        if runtime["event_sources"].get(event_source_id, {}).get("paused"):
            return {"event_source_id": event_source_id, "paused": True, "matched_count": 0,
                    "consumed_count": 0, "results": []}
        accepted = trigger_paths.accepted_input(lineage_id, normalized_input.id)
        if accepted and accepted.get("identity") != normalized_input.identity:
            # Reconciliation re-observes a fact under its own actor. The first
            # accepted initiator owns this occurrence and its recovery.
            return {"event_source_id": event_source_id, "duplicate": True, "matched_count": 0,
                    "consumed_count": 0, "results": [],
                    "trigger_path_id": trigger_paths.storage.path_id(lineage_id, "event", normalized_input.id)}
        trigger_path_id = trigger_paths.begin_event(
            lineage_id, event_source_id, normalized_input.to_dict(), cause_kind=cause_kind, cause_id=cause_id,
        )
    dispatch_event = normalized_input.to_dict()
    dispatch_event["payload"] = {
        **dict(dispatch_event.get("payload") or {}),
        "trigger_path_id": trigger_path_id,
    }
    try:
        with lineage_instruction_context.event_scope(normalized_input, actor_id):
            result = _dispatch_lineage_program_event_locked(
                lineage_id,
                event_source_id,
                dispatch_event,
                actor,
                cursor=cursor,
                expected_event_source_contract=(
                    expected_event_source_contract
                ),
                event_source_override=event_source_override,
                selected_wire_ids=selected_wire_ids,
                preclaimed_wire_ids=preclaimed_wire_ids,
                bypass_unavailable_policy=bypass_unavailable_policy,
                invocation_arguments=invocation_arguments,
                authority=authority,
            )
    except Exception as exc:
        trigger_paths.event_settled(trigger_path_id, event_source_id, normalized_input.id, error=str(exc))
        raise
    normalized_event = lineage_primitives.normalize_event(
        result.get("event") or dispatch_event
    )
    result["trigger_path_id"] = trigger_path_id
    fire_result = result.get("fire")
    if isinstance(fire_result, dict) and fire_result.get("status") == "fired":
        lineage_program_runtime.record_event_delivery_summary(
            lineage_id,
            event_source_id,
            (
                result.get("results")
                if isinstance(result.get("results"), list)
                else []
            ),
            event_id=normalized_event.id,
            trigger_path_id=trigger_path_id,
            event=normalized_event.to_dict(),
        )
    with authority.lineage_scope(lineage_id):
        current = authority.read_lineage_state(lineage_id)
        plan = _compiled_program(current)

    fire = fire_result if isinstance(fire_result, dict) else {}
    if (
        fire.get("status") == "fired"
        and fire.get("exhausted")
    ):
        result["budget_exhausted_events"] = (
            _dispatch_program_runtime_event_watchers(
                lineage_id,
                plan,
                actor,
                event_type="budget.exhausted",
                observed_event_source_id=event_source_id,
                original_event=normalized_event,
                payload={
                    "fire_count": int(fire.get("occurrence_count") or 0),
                    "max_fires": int(fire.get("max_fires") or 0),
                    "generation": int(fire.get("generation") or 0),
                },
                authority=authority,
                cursor_suffix=str(fire.get("generation") or 0),
            )
        )
    failed_deliveries = [
        item
        for item in result.get("results") or []
        if (
            isinstance(item, dict)
            and str(item.get("status") or "")
            in {"failed", "queue_full"}
        )
    ]
    if failed_deliveries:
        failed_wire_ids = sorted({
            str(item.get("wire_id") or "")
            for item in failed_deliveries
            if str(item.get("wire_id") or "")
        })
        result["delivery_failed_events"] = (
            _dispatch_program_runtime_event_watchers(
                lineage_id,
                plan,
                actor,
                event_type="event.delivery_failed",
                observed_event_source_id=event_source_id,
                original_event=normalized_event,
                payload={
                    "wire_ids": failed_wire_ids,
                    "failure_count": len(failed_deliveries),
                    "diagnostics": [
                        _text(
                            item.get("error") or item.get("reason"),
                            500,
                        )
                        for item in failed_deliveries[:20]
                    ],
                },
                wire_ids=failed_wire_ids,
                authority=authority,
                cursor_suffix=hashlib.sha256(
                    "\0".join(failed_wire_ids).encode("utf-8")
                ).hexdigest()[:16],
            )
        )
    if fire.get("status") == "fired":
        lineage_program_runtime.mark_event_fanout_complete(
            lineage_id, event_source_id, generation=int(fire.get("generation") or 0),
            occurrence_count=int(fire.get("occurrence_count") or 0),
        )
    trigger_paths.event_settled(trigger_path_id, event_source_id, normalized_input.id,
                               error="delivery failed" if failed_deliveries else "")
    return result

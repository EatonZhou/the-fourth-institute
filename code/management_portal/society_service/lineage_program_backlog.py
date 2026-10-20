"""Program pending-wire selection and replay delivery.

The aggregate supplies an authorized snapshot and retains its state lock and
commit authority. Runtime claims and external dispatch happen after unlock.
"""
from __future__ import annotations

from management_common.operation_errors import OperationError

from dataclasses import dataclass
from typing import Any, Callable

from management_common.principal import OperationIdentity

from . import lineage_instruction_context, lineage_primitives
from . import lineage_event_delivery


def select_route(
    lineage: dict[str, Any],
    plan: dict[str, Any],
    runtime_view: dict[str, dict[str, Any]],
    can_consume: Callable[[dict[str, Any]], bool],
) -> tuple[str, str, dict[str, Any]] | None:
    event_sources = plan.get("event_sources") if isinstance(plan.get("event_sources"), dict) else {}
    routes_by_wire = {
        str(route.get("wire_id") or ""): (str(event_source_id), dict(route))
        for event_source_id, source in event_sources.items()
        if isinstance(source, dict)
        for route in source.get("subscriber_routes") or []
        if isinstance(route, dict) and str(route.get("wire_id") or "")
    }
    nodes_by_id = {
        str(node.get("id") or ""): node
        for node in lineage.get("nodes") or []
        if isinstance(node, dict)
    }
    for wire in plan.get("wires") or []:
        if (not isinstance(wire, dict) or str(wire.get("kind") or "") != "route"
                or not str(wire.get("id") or "")):
            continue
        target_id = str(wire.get("target") or "")
        target = nodes_by_id.get(target_id)
        if not target:
            continue
        if str(wire["id"]) in routes_by_wire:
            continue
        routes_by_wire[str(wire["id"])] = (
            str(wire.get("source") or ""),
            {
                "wire_id": str(wire["id"]), "target_id": target_id,
                "target_kind": "agent",
                "config": dict(wire.get("config") or {}),
            },
        )
    pending_wire_ids = sorted(
        (wire_id for wire_id, runtime in runtime_view.items()
         if int(runtime.get("pending_count") or 0) > 0),
        key=lambda wire_id: (
            str((runtime_view.get(wire_id) or {}).get("oldest_queued_at") or ""), wire_id,
        ),
    )
    for wire_id in pending_wire_ids:
        route_entry = routes_by_wire.get(wire_id)
        if route_entry:
            source_id, route = route_entry
            if can_consume(route):
                return wire_id, source_id, route
    return None


@dataclass(frozen=True)
class ReplayDelivery:
    # Checks the saved actor against the current Lineage and returns that Lineage.
    authorize: Callable[[str], dict[str, Any]]
    has_registered_invocation: Callable[[str, str, dict[str, Any]], bool]
    packet: Callable[[dict[str, Any], str, Any, dict[str, Any], str], dict[str, Any]]
    event: Callable[..., dict[str, Any]]


def deliver_claimed(
    wire_id: str,
    event_source_id: str,
    route: dict[str, Any],
    pending: dict[str, Any],
    ports: ReplayDelivery,
) -> dict[str, Any]:
    replay_event = lineage_primitives.normalize_event(pending.get("event") or {})
    if not replay_event.identity:
        raise PermissionError("Pending Event has no verified identity; explicit reissue is required")
    replay_actor = OperationIdentity.from_dict(replay_event.identity).principal.id
    with lineage_instruction_context.event_scope(replay_event, replay_actor):
        current = ports.authorize(replay_actor)
        target_id = str(route.get("target_id") or "")
        if ports.has_registered_invocation(wire_id, target_id, pending):
            return {"status": "already_delivered", "wire_id": wire_id, "target_id": target_id}
        if isinstance((pending.get("invocation") or {}).get("packet"), dict):
            invocation = dict(pending.get("invocation") or {})
            if "prompt_override" in pending:
                invocation["prompt"] = pending["prompt_override"]
            return {
                "event": replay_event.to_dict(),
                "results": [{
                    "wire_id": wire_id,
                    **ports.packet(current, target_id, replay_event, invocation, replay_actor),
                }],
            }
        result = ports.event(
            event_source_id, pending.get("event") or {}, replay_actor,
            cursor=str(pending.get("cursor") or ""),
            selected_wire_ids={wire_id}, preclaimed_wire_ids={wire_id},
            bypass_unavailable_policy=True,
            invocation_arguments=(
                {wire_id: {"prompt": pending["prompt_override"]}}
                if "prompt_override" in pending else None
            ),
        )
        return result


# The aggregate supplies only state, authorization and delivery capabilities.
from contextlib import AbstractContextManager
from typing import Protocol
from . import lineage_program_event_dispatch, lineage_program_execution, lineage_program_runtime
from .lineage_program_queries import _compiled_program
from .lineage_program_runtime_projection import _program_invoke_wire_runtime_view
from .lineage_program_contracts import _program_event_source_contract_fingerprint
from .lineage_values import _safe_actor, _text
import hashlib
import functools
import secrets

class ProgramControlAuthority(Protocol):
    def lineage_scope(self, lineage_id: str) -> AbstractContextManager[None]: ...
    def read_lineage(self, lineage_id: str, *, actor: str) -> dict[str, Any]: ...
    def ensure_manager(self, lineage: dict[str, Any], actor: str) -> None: ...
    def event_authority(self) -> lineage_program_event_dispatch.ProgramEventAuthority: ...
    def program_execution_ports(self) -> lineage_program_execution.ProgramExecutionPorts: ...
    def dispatch_lineage_program_event(self, lineage_id: str, event_source_id: str, event: lineage_primitives.LineageEvent, actor: str, **options: Any) -> dict[str, Any]: ...
    def has_registered_invocation(self, lineage_id: str, wire_id: str, target_id: str, occurrence: dict[str, Any]) -> bool: ...
    def retire_exhausted_sources(self, lineage_id: str, actor: str) -> list[str]: ...
    def observe_trigger_path(self, lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]: ...
    def get_lineage(self, lineage_id: str, actor: str) -> dict[str, Any]: ...

def dispatch_expired_deliveries(
    lineage_id: str,
    actor: str,
    *,
    authority: ProgramControlAuthority,
) -> list[dict[str, Any]]:
    """Each expiry retains its saved authority, independently of the polling caller."""
    from . import trigger_paths

    expired = lineage_program_runtime.drain_expired_deliveries(lineage_id)
    results: list[dict[str, Any]] = []
    for item in expired:
        source_component_id = str(item.get("event_source_id") or "")
        if "::" in source_component_id:
            continue
        original_event = item.get("event") if isinstance(item.get("event"), dict) else {}
        wire_id = str(item.get("wire_id") or "")
        try:
            normalized = lineage_primitives.normalize_event(original_event)
            if not normalized.identity:
                raise PermissionError("Expired Event has no verified identity; explicit reissue is required")
            replay_actor = OperationIdentity.from_dict(normalized.identity).principal.id
            with lineage_instruction_context.event_scope(normalized, replay_actor):
                with authority.lineage_scope(lineage_id):
                    lineage = authority.read_lineage(lineage_id, actor=replay_actor)
                    authority.ensure_manager(lineage, replay_actor)
                    plan = _compiled_program(lineage)
                results.extend(
                    lineage_program_event_dispatch._dispatch_program_runtime_event_watchers(
                        lineage_id, plan, replay_actor,
                        event_type="event.delivery_expired",
                        observed_event_source_id=source_component_id,
                        original_event=normalized,
                        payload={"wire_id": wire_id, "wire_ids": [wire_id] if wire_id else [],
                                 "expired_at": str(item.get("expired_at") or "")},
                        wire_ids=[wire_id] if wire_id else [],
                        authority=authority.event_authority(),
                        cursor_suffix=hashlib.sha256(str(item.get("cursor") or "").encode("utf-8")).hexdigest()[:16],
                    )
                )
        except (KeyError, PermissionError, ValueError) as exc:
            # The drained fact gets a durable failure even if its User is now
            # disabled or an old record has no identity. Never synthesize one.
            path_id = trigger_paths.begin_event(
                lineage_id, source_component_id, original_event,
                cause_kind="external", cause_id="expired:" + str(item.get("cursor") or original_event.get("id") or wire_id),
            )
            trigger_paths.event_settled(path_id, source_component_id, str(original_event.get("id") or ""), error=str(exc))
            results.append({"status": "failed", "wire_id": wire_id, "event": original_event,
                            "trigger_path_id": path_id, "error": str(exc)})
    return results


def drain_event_backlog(
    lineage_id: str,
    actor: str,
    *,
    max_events: int = 32,
    authority: ProgramControlAuthority,
) -> dict[str, Any]:
    """Deliver queued wire occurrences in FIFO order as targets become idle."""
    _lineage_scope = authority.lineage_scope
    _read_lineage = authority.read_lineage
    _ensure_manager = authority.ensure_manager
    _program_event_authority = authority.event_authority
    _program_execution_ports = authority.program_execution_ports
    dispatch_lineage_program_event = authority.dispatch_lineage_program_event
    _pending_event_has_registered_invocation = authority.has_registered_invocation
    _retire_exhausted_event_sources = authority.retire_exhausted_sources

    actor_id = _safe_actor(actor)
    expired_events = dispatch_expired_deliveries(
        lineage_id,
        actor_id,
        authority=authority,
    )
    delivered_count = 0
    attempted_count = 0
    # A wire whose head was not delivered, is waiting for its retry or is
    # being delivered elsewhere sits out this drain; the other wires keep going.
    held_wire_ids: set[str] = set()
    waiting_attempt_count = 0
    # A closed Event Source or Effect keeps its queued Events until it opens.
    # Opening notifies the Lineage, so they are not claimed meanwhile.
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    closed_ids = {
        component_id for group in ("event_sources", "operators")
        for component_id, state in (runtime.get(group) or {}).items()
        if isinstance(state, dict) and state.get("paused")
    }
    results: list[dict[str, Any]] = []
    # Read again only after a delivery attempt; a claim that finds nothing
    # changes no state, so the remaining wires are chosen from the same read.
    lineage: dict[str, Any] = {}
    stale = True
    # Nothing is left that this drain could deliver.
    complete = False
    for _index in range(max(1, min(128, int(max_events or 32)))):
        with _lineage_scope(lineage_id):
            if stale:
                lineage = _read_lineage(lineage_id, actor=actor)
                _ensure_manager(lineage, actor_id)
                plan = _compiled_program(lineage)
                runtime_view = _program_invoke_wire_runtime_view(lineage)
                stale = False
            selected = select_route(
                lineage, plan, runtime_view,
                lambda route: str(route.get("wire_id") or "") not in held_wire_ids
                and str(route.get("target_id") or "") not in closed_ids
                and str((runtime_view.get(str(route.get("wire_id") or "")) or {}).get("oldest_event_source_id") or "")
                not in closed_ids
                and lineage_program_event_dispatch._program_event_route_can_consume(
                    lineage_id, lineage, route, actor_id,
                    authority=_program_event_authority(),
                )[0],
            )
        if selected is None:
            complete = True
            break
        wire_id, event_source_id, selected_route = selected
        pending = lineage_program_runtime.claim_pending_event(
            lineage_id,
            wire_id, event_source_id=event_source_id,
            target_id=str(selected_route.get("target_id") or ""),
        )
        if not pending or not pending.get("dispatch_token"):
            held_wire_ids.add(wire_id)
            continue
        stale = True
        attempted_count += 1
        dispatch_token = str(pending.get("dispatch_token") or "")
        def current_replay_lineage(replay_actor: str) -> dict[str, Any]:
            with _lineage_scope(lineage_id):
                current = _read_lineage(lineage_id, actor=replay_actor)
                _ensure_manager(current, replay_actor)
                return current

        settled = lineage_event_delivery.deliver_and_settle(
            lineage_id, wire_id, str(selected_route.get("target_id") or ""), dispatch_token,
            lambda: deliver_claimed(
                wire_id, event_source_id, selected_route, pending,
                ReplayDelivery(
                    authorize=current_replay_lineage,
                    has_registered_invocation=lambda wire, target, occurrence: (
                        _pending_event_has_registered_invocation(lineage_id, wire, target, occurrence)
                    ),
                    packet=lambda *args: lineage_program_execution._dispatch_program_packet_to_agent(
                        *args, ports=_program_execution_ports()),
                    event=functools.partial(dispatch_lineage_program_event, lineage_id),
                ),
            ),
        )
        delivered, delivery_error, hold = settled.delivered, settled.error, settled.hold
        results.append({"wire_id": wire_id, "delivered": delivered, "dispatch": settled.result,
                        **({"error": delivery_error} if delivery_error else {})})
        if hold:
            # Held for its resolution: the wire's later occurrences go on now.
            continue
        if not delivered:
            held_wire_ids.add(wire_id)
            if not delivery_error:
                waiting_attempt_count = max(waiting_attempt_count, int(pending.get("attempt_count") or 1))
            continue
        delivered_count += 1
    # Failed attempts wait for their stored retry time; a target that is
    # closed or busy is looked at again after the same delay.
    delays = [
        lineage_program_runtime.next_retry_seconds(lineage_id) if held_wire_ids else 0,
        lineage_program_runtime.retry_delay_seconds(waiting_attempt_count) if waiting_attempt_count else 0,
    ]
    retry_after_seconds = min((delay for delay in delays if delay > 0), default=0)
    # Retiring re-reads the Lineage under its admission lock; skip it when no
    # Event Source asks to be deleted once exhausted.
    retires = any(
        component.get("kind") == "event_source"
        and (component.get("config") or {}).get("auto_delete_on_exhaustion")
        for component in (lineage.get("program") or {}).get("components") or []
        if isinstance(component, dict)
    )
    deleted_event_source_ids = _retire_exhausted_event_sources(lineage_id, actor_id) if retires else []
    return {
        "deleted_event_source_ids": deleted_event_source_ids,
        "attempted_count": attempted_count,
        "delivered_count": delivered_count,
        "expired_count": len(expired_events),
        "expired_events": expired_events,
        "results": results,
        "retry_after_seconds": retry_after_seconds,
        "complete": complete,
    }


class EventSourcePausedError(OperationError):
    http_status = 409
    commit_state = "not_committed"
    code = "instruction.conflict"


def emit_manual_event(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    authority: ProgramControlAuthority,
) -> dict[str, Any]:
    """Trigger one ``manual.triggered`` Event Source, or only one of its wires."""
    _lineage_scope = authority.lineage_scope
    _read_lineage = authority.read_lineage
    _ensure_manager = authority.ensure_manager
    dispatch_lineage_program_event = authority.dispatch_lineage_program_event
    observe_trigger_path = authority.observe_trigger_path
    get_lineage = authority.get_lineage

    actor_id = _safe_actor(actor)
    event_source_id = _text(payload.get("event_source_id"), 110).lower()
    wire_id = _text(payload.get("wire_id"), 110).lower()
    if not event_source_id and not wire_id:
        raise ValueError("触发 Event 必须指定 Event Source 或连线")
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor_id)
        plan = _compiled_program(lineage)
        if wire_id:
            wire = next(
                (item for item in plan.get("wires") or [] if str(item.get("id") or "") == wire_id),
                None,
            )
            if not wire or str(wire.get("kind") or "") != "route":
                raise KeyError("Event 连线不存在")
            wire_source = str(wire.get("source") or "")
            if event_source_id and event_source_id != wire_source:
                raise ValueError("连线不属于这个 Event Source")
            event_source_id = wire_source
        event_source = (plan.get("event_sources") or {}).get(event_source_id)
        if not isinstance(event_source, dict) or event_source.get("kind") != "event_source":
            raise KeyError("program Event Source not found")
        if str(event_source.get("event_type") or "") != "manual.triggered":
            raise ValueError("TriggerEvent 只能触发 manual.triggered 事件源")
        source_contract = _program_event_source_contract_fingerprint(event_source)
        occurrence = secrets.token_hex(12)
        event_payload: dict[str, Any] = {"triggered_by": actor_id, "prompt": payload.get("prompt")}
        if "content_parts" in payload:
            event_payload["content_parts"] = payload["content_parts"]
        if wire_id:
            event_payload["edge_id"] = wire_id
        if payload.get("event_name"):
            event_payload["name"] = payload["event_name"]
        if isinstance(payload.get("payload"), dict):
            event_payload["data"] = payload["payload"]
        from management_portal.hatg.kernel import current_instruction_id
        instruction = current_instruction_id()
        event_payload["cause"] = ({"kind": "instruction", "id": instruction} if instruction
                                  else {"kind": "input", "id": occurrence})
        event = lineage_primitives.make_event(
            "manual.triggered",
            subject_resident_id=_text(payload.get("subject_resident_id"), 80).lower(),
            payload=event_payload,
            correlation=f"{event_source_id}:{occurrence}",
        )
    dispatched = dispatch_lineage_program_event(
        lineage_id,
        event_source_id,
        event,
        actor_id,
        cursor=f"manual:{occurrence}",
        expected_event_source_contract=source_contract,
        **({"selected_wire_ids": {wire_id}} if wire_id else {}),
    )
    if dispatched.get("paused"):
        raise EventSourcePausedError("事件源已暂停，恢复后才能触发")
    backlog = drain_event_backlog(
        lineage_id,
        actor_id,
        authority=authority,
    )
    trigger_path_id = str(dispatched.get("trigger_path_id") or "")
    observed = observe_trigger_path(lineage_id, {"trigger_path_id": trigger_path_id, "limit": 50}, actor_id)
    return {**dispatched, "trigger_path": observed, "program_event_backlog": backlog,
            "lineage": get_lineage(lineage_id, actor_id)}

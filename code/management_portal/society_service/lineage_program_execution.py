"""Program Operator and Resident execution policy. Aggregate writes remain in the supplied authority ports."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .lineage_program_effect_execution import ProgramEffectAuthority
    from .lineage_program_event_dispatch import ProgramEventAuthority

from . import (
    lineage_instruction_context,
    lineage_operator_runtime,
    lineage_primitives,
    lineage_program_arguments,
    lineage_program_identity,
    lineage_program_operator_execution,
    lineage_program_packet_routing,
    lineage_program_resident_inputs,
    lineage_program_vocabulary,
    resident_message_contract,
    trigger_paths,
    wire_delivery_budget,
)
from management_common.limit_contract import normalize_optional_limit
from .lineage_event_claims import (
    defer_program_event_route as _defer_program_event_route,
)
from .lineage_event_claims import prepare_operator_inboxes
from .lineage_program_contracts import _program_event_source_contract_fingerprint
from .lineage_program_packet_routing import (
    compiled_invocation as _program_compiled_invocation,
)
from .lineage_program_packet_routing import (
    resolved_invoke_arguments as _program_resolved_invoke_arguments,
)
from .lineage_program_queries import (
    _compiled_program,
    _program_operator_barriers,
    _program_packet_wires,
)
from .lineage_values import _bool, _clone_json


LOGGER = logging.getLogger(__name__)


class ProgramExecutionState(Protocol):
    def lineage_execution_authority(self, lineage_id: str) -> AbstractContextManager[None]: ...
    def lineage_scope(self, lineage_id: str, *, operation: str = "") -> AbstractContextManager[None]: ...
    def read_lineage(self, lineage_id: str, *, actor: str) -> dict[str, Any]: ...
    def ensure_manager(self, lineage: dict[str, Any], actor: str) -> None: ...
    def now_iso(self) -> str: ...
    def resume_program_execution_principal(self, lineage_id: str, event: lineage_primitives.LineageEvent, resident_id: str) -> str: ...


class ProgramResidentDelivery(Protocol):
    def send_node_message(self, node: dict[str, Any], prompt: str, actor: str, **options: Any) -> object: ...
    def register_program_agent_invocation(self, lineage_id: str, target_id: str, message_id: str, event: lineage_primitives.LineageEvent, source_wire_id: str, actor: str) -> dict[str, Any]: ...
    def notify_program_resident_invocation_terminal(self, lineage_id: str, resident_id: str, message_id: str, invocations: list[dict[str, Any]], *, status: str) -> bool: ...
    def completed_message_evidence(self, node: dict[str, Any], message_id: str, actor: str) -> dict[str, Any]: ...


class ProgramExecutionFlow(Protocol):
    def dispatch_lineage_program_event(self, lineage_id: str, event_source_id: str, event: lineage_primitives.LineageEvent | dict[str, Any], actor: str, **options: Any) -> dict[str, Any]: ...
    def event_authority(self) -> ProgramEventAuthority: ...
    def effect_authority(self) -> ProgramEffectAuthority: ...


@dataclass(frozen=True)
class ProgramExecutionPorts:
    state: ProgramExecutionState
    resident: ProgramResidentDelivery
    flow: ProgramExecutionFlow


def _make_program_effect_invocation(
    event: lineage_primitives.LineageEvent,
    *,
    source_kind: str,
    source_id: str = "",
    source_wire_id: str = "",
    arguments: dict[str, Any] | None = None,
    argument_mapping: dict[str, Any] | None = None,
    argument_overrides: dict[str, Any] | None = None,
    prompt: object = "",
    from_resident_id: str = "",
    packet: dict[str, Any] | None = None,
    include_envelope: bool = True,
) -> lineage_primitives.EffectInvocation:
    """Adapt one Route packet to the Effect runtime input."""

    dynamic_arguments = dict(
        event.payload.get("arguments")
        if isinstance(event.payload.get("arguments"), dict)
        else {}
    )
    if isinstance(arguments, dict):
        dynamic_arguments.update(arguments)
    input_prompt = (
        lineage_primitives.normalize_invocation_prompt(prompt)
        or lineage_primitives.normalize_invocation_prompt(dynamic_arguments.get("prompt"))
        or lineage_primitives.normalize_invocation_prompt(event.payload.get("prompt"))
    )
    if input_prompt:
        dynamic_arguments["prompt"] = input_prompt
    if "content_parts" in event.payload:
        dynamic_arguments["content_parts"] = event.payload["content_parts"]
    if isinstance(packet, dict) and (packet.get("message") or {}).get("content_parts"):
        dynamic_arguments["content_parts"] = packet["message"]["content_parts"]
    envelope = (
        packet
        if isinstance(packet, dict)
        else lineage_primitives.event_packet(event).to_dict()
    )
    if isinstance(argument_mapping, dict):
        dynamic_arguments.update(
            lineage_program_arguments.resolve_invocation_argument_mapping(argument_mapping, event, packet=envelope)
        )
    if argument_overrides:
        dynamic_arguments.update(lineage_primitives.normalize_invocation_arguments(argument_overrides))
    return lineage_primitives.make_effect_invocation(
        event,
        arguments=dynamic_arguments,
        source_kind=source_kind,
        source_id=source_id,
        source_wire_id=source_wire_id,
        from_resident_id=from_resident_id,
        include_envelope=include_envelope,
        packet=envelope,
    )


def program_event_route_can_consume(
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
    if target_kind in lineage_program_vocabulary.PACKET_OPERATOR_KINDS:
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


def try_dispatch_operator(
    lineage_id: str, actor: str, operator_id: str, event: lineage_primitives.LineageEvent,
    *, ports: ProgramExecutionPorts,
) -> dict[str, Any]:
    step = trigger_paths.operator_started(lineage_id, operator_id, event.to_dict())
    forwarded = lineage_primitives.normalize_event({
        **event.to_dict(), "payload": {**event.payload, "trigger_step_id": step},
    })
    try:
        result = _dispatch_ready_operator(lineage_id, actor, operator_id, forwarded, ports=ports)
    except Exception as exc:
        trigger_paths.operator_settled(step, status="failed", result={"error": str(exc)})
        raise
    state = str(result.get("status") or "")
    status = "waiting" if state == "waiting" else "failed" if state == "failed" else "cancelled" if state == "cancelled" else "completed"
    trigger_paths.operator_settled(step, status=status)
    return result


def _ready_inbox(
    lineage_id: str,
    operator_id: str,
    event: lineage_primitives.LineageEvent,
) -> tuple[dict[str, Any] | None, dict[str, Any], set[str]]:
    """Claim the operator's inbox for the Event once every expected wire has delivered.

    Returns (result, claimed inbox, expected wire IDs); ``result`` is set when
    there is nothing to run: the Event was cancelled, still waits, or another
    dispatcher claimed it.
    """

    runtime = lineage_operator_runtime.read_operator_runtime(
        lineage_id,
        operator_id,
    )
    inbox = (
        (runtime.get("inbox") or {}).get(event.id)
        if isinstance(runtime.get("inbox"), dict)
        else None
    )
    if not isinstance(inbox, dict):
        return {"status": "cancelled", "event_id": event.id}, {}, set()
    expected_ids = {
        str(item) for item in inbox.get("expected_wire_ids") or []
    }
    inputs = (
        inbox.get("inputs")
        if isinstance(inbox.get("inputs"), dict)
        else {}
    )
    if not expected_ids or not expected_ids.issubset(set(inputs)):
        return {
            "status": "waiting",
            "event_id": event.id,
            "received": len(set(inputs) & expected_ids),
            "expected": len(expected_ids),
        }, {}, expected_ids
    claimed = lineage_operator_runtime.pop_inbox(
        lineage_id,
        operator_id,
        event.id,
    )
    if not claimed:
        return {"status": "claimed", "event_id": event.id}, {}, expected_ids
    return None, claimed, expected_ids


@dataclass(frozen=True)
class _OperatorScope:
    """One Packet Operator as the Lineage defines it when its inbox is claimed."""

    nodes: dict[str, dict[str, Any]]
    plan: dict[str, Any]
    operator: dict[str, Any]
    lineage_snapshot: dict[str, Any]
    kind: str
    config: dict[str, Any]
    snapshot: dict[str, Any]


def _operator_scope(
    lineage_id: str,
    actor: str,
    operator_id: str,
    *,
    ports: ProgramExecutionPorts,
) -> _OperatorScope:
    with ports.state.lineage_scope(lineage_id):
        lineage = ports.state.read_lineage(lineage_id, actor=actor)
        ports.state.ensure_manager(lineage, actor)
        nodes = {
            str(node.get("id") or ""): node
            for node in lineage.get("nodes") or []
            if isinstance(node, dict)
        }
        plan = _compiled_program(lineage)
        operator = (plan.get("packet_operators") or {}).get(operator_id) or {}
        if not operator:
            raise ValueError("Packet Operator component 已不存在")
        lineage_snapshot = _clone_json(lineage)
        operator_kind = str(operator.get("kind") or "")
        operator_config = _clone_json(operator.get("config") or {})
        operator_snapshot = {
            "id": operator_id,
            "title": str(operator.get("title") or operator_id),
            "config": {**operator_config, "mode": operator_kind},
        }
    return _OperatorScope(nodes, plan, operator, lineage_snapshot, operator_kind, operator_config, operator_snapshot)


def _ask_resident_executor(
    lineage_id: str,
    actor: str,
    operator_id: str,
    event: lineage_primitives.LineageEvent,
    scope: _OperatorScope,
    claimed: dict[str, Any],
    expected_ids: set[str],
    operator_inputs: lineage_program_operator_execution.OperatorInputs,
    *,
    ports: ProgramExecutionPorts,
) -> str:
    """Send the operator's prompt to its executor Resident and remember the turn; the turn's message ID."""

    prompt = lineage_program_operator_execution.resident_prompt(
        lineage_id, operator_id, scope.kind, scope.snapshot,
        event, operator_inputs, scope.nodes, scope.plan,
    )
    executor_resident_id = str(
        scope.operator.get("resident_executor_id") or ""
    )
    executor_snapshot = scope.nodes.get(executor_resident_id)
    if not executor_snapshot:
        raise ValueError("Resident 式 Packet Operator 的执行器已不存在")
    delivery_message_id = lineage_program_identity.delivery_message_id(
        lineage_id,
        event.id,
        f"operator:{operator_id}:{','.join(sorted(expected_ids))}",
        executor_resident_id,
    )
    response = ports.resident.send_node_message(
        executor_snapshot,
        prompt,
        actor,
        message_id=delivery_message_id,
        instruction_context=lineage_instruction_context.resident_context(
            lineage_id, event, actor, executor_resident_id,
        ),
    )
    expected_execution_id = resident_message_contract.delivered_message_id(response)
    if not expected_execution_id:
        raise RuntimeError(
            "Packet Operator 消息已发送，但无法确认对应的 Resident job"
        )
    lineage_operator_runtime.update_pending_delivery(
        lineage_id,
        operator_id,
        lineage_program_operator_execution.pending_delivery(
            operator_id,
            scope.kind,
            executor_resident_id,
            expected_execution_id,
            event,
            claimed,
            operator_inputs,
            list(scope.operator.get("outgoing_packet_wire_ids") or []),
        ),
    )
    ports.resident.register_program_agent_invocation(
        lineage_id,
        executor_resident_id,
        expected_execution_id,
        event,
        operator_id,
        actor,
    )
    return expected_execution_id


def _dispatch_ready_operator(
    lineage_id: str,
    actor: str,
    operator_id: str,
    event: lineage_primitives.LineageEvent,
    *,
    ports: ProgramExecutionPorts,
) -> dict[str, Any]:
    not_ready, claimed, expected_ids = _ready_inbox(lineage_id, operator_id, event)
    if not_ready is not None:
        return not_ready
    scope = _operator_scope(lineage_id, actor, operator_id, ports=ports)
    operator_inputs = lineage_program_operator_execution.inputs_from_claim(
        claimed, scope.nodes
    )
    implementation = str(
        scope.config.get("operator_implementation") or "rules"
    )
    try:
        if implementation == "rules" or not operator_inputs.packets:
            output = lineage_program_operator_execution.evaluate_rules(
                scope.kind, scope.config, operator_inputs.packets
            )
            routed = dispatch_program_packets(
                scope.lineage_snapshot,
                scope.plan,
                operator_id,
                event,
                output.packets,
                actor,
                packet_wires=_program_packet_wires(
                    scope.lineage_snapshot,
                    operator_id,
                ),
                packets_by_source_port=output.by_source_port,
                ports=ports,
            )
            return {
                "status": lineage_program_operator_execution.completed_status(scope.kind),
                "event_id": event.id,
                "packet_wire_ids": sorted(expected_ids),
                **routed,
            }
        expected_execution_id = _ask_resident_executor(
            lineage_id, actor, operator_id, event, scope, claimed, expected_ids, operator_inputs, ports=ports,
        )
    except Exception as exc:  # noqa: BLE001 - persist failure at the operator boundary
        LOGGER.exception("Packet Operator %s of %s failed on Event %s; the Event is cancelled for it",
                         operator_id, lineage_id, event.id)
        # The inbox was claimed as one immutable wait-all barrier; a turn
        # Portal cannot correlate cancels the whole Event for this operator.
        lineage_operator_runtime.cancel_event(
            lineage_id,
            operator_id,
            event.id,
        )
        return {
            "status": "failed",
            "event_id": event.id,
            "packet_wire_ids": sorted(expected_ids),
            "error": str(exc),
        }
    return {
        "status": lineage_program_operator_execution.working_status(scope.kind),
        "event_id": event.id,
        "message_id": expected_execution_id,
        "packet_wire_ids": sorted(expected_ids),
    }


@dataclass(frozen=True)
class _ExecutorTurn:
    """The operator a Resident turn ran for, as the Lineage defines it now."""

    operator_id: str
    operator: dict[str, Any]
    lineage_snapshot: dict[str, Any]
    executor_snapshot: dict[str, Any]
    plan: dict[str, Any]


def _executor_turn(
    lineage_id: str,
    executor_resident_id: str,
    actor: str,
    *,
    ports: ProgramExecutionPorts,
) -> _ExecutorTurn | None:
    """The Packet Operator the Resident executes; None when it executes none or either is gone."""

    with ports.state.lineage_scope(lineage_id):
        lineage = ports.state.read_lineage(lineage_id, actor=actor)
        nodes = {
            str(node.get("id") or ""): node
            for node in lineage.get("nodes") or []
            if isinstance(node, dict)
        }
        plan = _compiled_program(lineage)
        operator_id = str(
            (plan.get("operator_executors") or {}).get(
                executor_resident_id
            )
            or ""
        )
        current_operator = (
            (plan.get("packet_operators") or {}).get(operator_id) or {}
        )
        executor_node = nodes.get(executor_resident_id)
        if not operator_id or not current_operator or not executor_node:
            return None
        return _ExecutorTurn(operator_id, current_operator, _clone_json(lineage), dict(executor_node), plan)


def _claim_operator_delivery(
    lineage_id: str,
    turn: _ExecutorTurn,
    executor_resident_id: str,
    expected_execution_id: str,
    *,
    ports: ProgramExecutionPorts,
) -> tuple[dict[str, Any], lineage_primitives.LineageEvent, str] | None:
    """Take the operator's pending delivery the turn answers: (delivery, its Event, the principal it resumes as)."""

    runtime = lineage_operator_runtime.read_operator_runtime(
        lineage_id,
        turn.operator_id,
    )
    pending = (
        runtime.get("pending_deliveries", {}).get(expected_execution_id)
        if isinstance(runtime.get("pending_deliveries"), dict)
        else None
    )
    if (
        not isinstance(pending, dict)
        or not pending.get("program")
        or str(pending.get("executor_resident_id") or "")
        != executor_resident_id
    ):
        return None
    event = lineage_primitives.normalize_event(pending.get("event"))
    actor = ports.state.resume_program_execution_principal(lineage_id, event, executor_resident_id)
    pending = lineage_operator_runtime.pop_pending_delivery(
        lineage_id,
        turn.operator_id,
        expected_execution_id,
    )
    if not pending:
        return None
    return pending, event, actor


def _settle_operator_turn(
    lineage_id: str,
    executor_resident_id: str,
    expected_execution_id: str,
    status: str,
    output: dict[str, Any] | None = None,
    *,
    ports: ProgramExecutionPorts,
) -> None:
    """Settle the turn's handoff (anything but completed, failed or cancelled counts as cancelled) and notify."""

    ledger_status = (
        status
        if status in {"completed", "failed", "cancelled"}
        else "cancelled"
    )
    completed_at = ports.state.now_iso()
    invocations = lineage_program_resident_inputs.settle_resident_handoffs(
        lineage_id,
        message_id=expected_execution_id,
        resident_id=executor_resident_id,
        status=ledger_status,
        completed_at=completed_at,
        output=output,
    )
    ports.resident.notify_program_resident_invocation_terminal(
        lineage_id, executor_resident_id, expected_execution_id, invocations,
        status=ledger_status,
    )


def _operator_reply(
    turn: _ExecutorTurn,
    pending: dict[str, Any],
    expected_execution_id: str,
    actor: str,
    *,
    ports: ProgramExecutionPorts,
) -> tuple[dict[str, Any], str, lineage_program_operator_execution.OperatorOutput]:
    """The turn's evidence and the operator output its reply decides: (evidence, operator kind, output)."""

    evidence = ports.resident.completed_message_evidence(
        turn.executor_snapshot,
        expected_execution_id,
        actor,
    )
    reply = str(evidence.get("reply") or "")
    operator_kind = str(turn.operator.get("kind") or "")
    operator_config = turn.operator.get("config") or {}
    output = lineage_program_operator_execution.parse_resident_reply(
        operator_kind,
        reply,
        [
            dict(packet)
            for packet in pending.get("input_packets") or []
            if isinstance(packet, dict)
        ],
        operator_config,
    )
    return evidence, operator_kind, output


def _first_failed_route(routed: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in [
                *(routed.get("deliveries") or []),
                *(routed.get("operators") or []),
            ]
            if str(item.get("status") or "") == "failed"
        ),
        None,
    )


def complete_operator_invocation(
    lineage_id: str,
    executor_resident_id: str,
    expected_execution_id: str,
    actor: str,
    *,
    terminal_status: str = "completed",
    ports: ProgramExecutionPorts,
) -> dict[str, Any] | None:
    turn = _executor_turn(lineage_id, executor_resident_id, actor, ports=ports)
    if turn is None:
        return None
    claim = _claim_operator_delivery(lineage_id, turn, executor_resident_id, expected_execution_id, ports=ports)
    if claim is None:
        return None
    pending, event, actor = claim
    trigger_path_id = lineage_program_identity.event_path_id(lineage_id, event)
    if terminal_status != "completed":
        _settle_operator_turn(lineage_id, executor_resident_id, expected_execution_id, terminal_status, ports=ports)
        return {"status": "failed", "event_id": event.id, "trigger_path_id": trigger_path_id}

    if lineage_program_operator_execution.has_changed_output_topology(
        pending, turn.operator
    ):
        _settle_operator_turn(lineage_id, executor_resident_id, expected_execution_id, "discarded", ports=ports)
        return {
            "status": "discarded",
            "event_id": event.id,
            "reason": "program_topology_changed",
        }
    routed: dict[str, Any] = {"deliveries": [], "operators": []}
    try:
        evidence, operator_kind, output = _operator_reply(
            turn, pending, expected_execution_id, actor, ports=ports,
        )
        event = trigger_paths.begin_resident_output(
            lineage_id, executor_resident_id, expected_execution_id, at=ports.state.now_iso(), actor=actor,
        )
        output_path_id = event.payload["trigger_path_id"]
        routed = dispatch_program_packets(
            turn.lineage_snapshot,
            turn.plan,
            turn.operator_id,
            event,
            output.packets,
            actor,
            packet_wires=_program_packet_wires(
                turn.lineage_snapshot,
                turn.operator_id,
            ),
            packets_by_source_port=output.by_source_port,
            ports=ports,
        )
        trigger_paths.event_settled(output_path_id, executor_resident_id, event.id)
        failed = _first_failed_route(routed)
        if failed:
            raise RuntimeError(
                str(failed.get("error") or "Packet Operator route failed")
            )
        _settle_operator_turn(lineage_id, executor_resident_id, expected_execution_id, "completed", evidence,
                              ports=ports)
        return {
            "status": lineage_program_operator_execution.completed_status(operator_kind),
            "event_id": event.id,
            "trigger_path_id": trigger_path_id,
            **routed,
        }
    except Exception as exc:  # noqa: BLE001 - record the terminal failure for recovery
        LOGGER.exception("Packet Operator %s of %s could not take turn %s of %s",
                         turn.operator_id, lineage_id, expected_execution_id, executor_resident_id)
        _settle_operator_turn(lineage_id, executor_resident_id, expected_execution_id, "failed",
                              {"error": str(exc)}, ports=ports)
        return {
            "status": "failed",
            "event_id": event.id,
            "trigger_path_id": trigger_path_id,
            "error": str(exc),
            **routed,
        }


def complete_agent_invocations(
    lineage_id: str,
    source_node_id: str,
    source_node: dict[str, Any],
    message_id: str,
    actor: str,
    completed_at: str,
    *,
    ports: ProgramExecutionPorts,
) -> dict[str, Any]:
    """Adapt an ordinary Resident terminal output to Packet and Run facts."""
    _lineage_scope = ports.state.lineage_scope
    _read_lineage = ports.state.read_lineage
    _notify_program_resident_invocation_terminal = ports.resident.notify_program_resident_invocation_terminal
    _completed_message_evidence = ports.resident.completed_message_evidence
    dispatch_lineage_program_event = ports.flow.dispatch_lineage_program_event

    try:
        evidence = _completed_message_evidence(source_node, message_id, actor)
    except (KeyError, PermissionError, ValueError):
        evidence = {}
    invocations = lineage_program_resident_inputs.settle_resident_handoffs(
        lineage_id,
        message_id=message_id,
        resident_id=source_node_id,
        status="completed",
        completed_at=completed_at,
        output=evidence,
    )
    # Chat-initiated turns have no incoming HATG invocation; their Output
    # still starts a fresh Packet path after the Resident completes.
    if invocations:
        _notify_program_resident_invocation_terminal(
            lineage_id,
            source_node_id,
            message_id,
            invocations,
            status="completed",
        )

    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        plan = _compiled_program(lineage)
        packet_wires = [
            wire
            for wire in plan.get("wires") or []
            if isinstance(wire, dict)
            and str(wire.get("kind") or "") == "route"
            and str(wire.get("source") or "") == source_node_id
        ]
        all_finished_sources = [
            (str(source_id), dict(source))
            for source_id, source in (plan.get("event_sources") or {}).items()
            if isinstance(source, dict)
            and str(source.get("event_type") or "")
            == "residents.all_finished"
        ]
        lineage_snapshot = _clone_json(lineage)

    packet_results: list[dict[str, Any]] = []
    finished_results: list[dict[str, Any]] = []
    reply = str(evidence.get("reply") or "").strip()
    if reply and packet_wires:
        event = trigger_paths.begin_resident_output(lineage_id, source_node_id, message_id, at=completed_at, actor=actor)
        output_path_id = event.payload["trigger_path_id"]
        structured = lineage_primitives.parse_reply_packets(reply)
        packet_results.append(
            dispatch_program_packets(
                lineage_snapshot,
                plan,
                source_node_id,
                event,
                ([item for item in structured] if structured else [
                    lineage_primitives.make_packet("default", reply,
                        data={"message_id": message_id, "trigger_path_id": output_path_id})
                ]),
                actor,
                packet_wires=packet_wires,
                prepare_barriers=True,
                ports=ports,
            )
        )
        trigger_paths.event_settled(output_path_id, source_node_id, event.id)
    for invocation in invocations:
        trigger_path_id = str(invocation.get("trigger_path_id") or "")
        run_invocations = lineage_program_resident_inputs.path_resident_inputs(
            lineage_id, trigger_path_id
        )
        for event_source_id, source in all_finished_sources:
            configured_path_id = str(source.get("trigger_path_id") or "$trigger_path.id")
            if configured_path_id not in {"$trigger_path.id", trigger_path_id}:
                continue
            expected = set(source.get("resident_ids") or [])
            statuses = {
                resident_id: [
                    str(item.get("status") or "")
                    for item in run_invocations
                    if str(item.get("resident_id") or "") == resident_id
                ]
                for resident_id in expected
            }
            if not expected or any(
                not values or any(value != "completed" for value in values)
                for values in statuses.values()
            ):
                continue
            finished_event = lineage_primitives.make_event(
                "residents.all_finished",
                payload={
                    "cause": trigger_paths.execution_cause(source_node_id, message_id),
                    "resident_ids": sorted(expected),
                    "resident_count": len(expected),
                },
                correlation=f"{event_source_id}:{trigger_path_id}",
            )
            result = dispatch_lineage_program_event(
                lineage_id,
                event_source_id,
                finished_event,
                actor,
                cursor=f"resident-path-finished:{trigger_path_id}",
                expected_event_source_contract=(
                    _program_event_source_contract_fingerprint(source)
                ),
            )
            finished_results.append(result)
    return {
        "lineage_id": lineage_id,
        "invocations": invocations,
        "packet_results": packet_results,
        "all_finished_results": finished_results,
    }


@dataclass(frozen=True)
class _PacketDispatch:
    """The compiled scope one Packet collection is routed in, and the steps per target kind."""

    lineage: dict[str, Any]
    plan: dict[str, Any]
    source_node_id: str
    event: lineage_primitives.LineageEvent
    actor: str
    state: str
    invocation_arguments: dict[str, Any] | None
    ports: ProgramExecutionPorts

    @property
    def lineage_id(self) -> str:
        return str(self.lineage.get("lineage_id") or "")

    def to_operator(self, batch: lineage_program_packet_routing.PacketWireBatch) -> dict[str, Any]:
        inbox = lineage_operator_runtime.update_inbox(
            self.lineage_id,
            batch.target_id,
            event=self.event,
            source_wire_id=batch.wire_id,
            source_node_id=self.source_node_id,
            packets=batch.packets,
            state=self.state,
            include_envelope=_bool(batch.config.get("include_envelope"), True),
        )
        if inbox is None:
            return {"status": "cancelled", "event_id": self.event.id, "operator_id": batch.target_id}
        return try_dispatch_operator(self.lineage_id, self.actor, batch.target_id, self.event, ports=self.ports)

    def to_loop(self, batch: lineage_program_packet_routing.PacketWireBatch, packet: dict[str, Any]) -> dict[str, Any]:
        from . import lineage_loop_runtime

        return lineage_loop_runtime.receive(
            self.lineage_id,
            self.plan,
            batch.target_id,
            str(batch.target_port or "invoke"),
            self.event,
            self.actor,
            dispatch_packets=self.loop_output,
            packet=packet,
        )

    def loop_output(self, loop_id: str, output: str, activation: lineage_primitives.LineageEvent, value: Any) -> dict[str, Any]:
        """Route one round's value out of a loop's continue or stop port."""

        return dispatch_program_packets(
            self.lineage, self.plan, loop_id, activation, [value], self.actor,
            packet_wires=[wire for wire in self.plan["wires"]
                          if wire["kind"] == "route" and wire["source"] == loop_id
                          and wire.get("source_port") == output],
            prepare_barriers=True,
            ports=self.ports,
        )

    def invocation_input(self, wire_id: str, packet: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """The arguments and prompt one Packet invokes its target with."""

        arguments = _program_resolved_invoke_arguments(self.plan, wire_id, self.event, packet=packet)
        if self.invocation_arguments:
            arguments.update(lineage_primitives.normalize_invocation_arguments(self.invocation_arguments))
        prompt = lineage_program_packet_routing.input_prompt(
            packet, arguments, _program_compiled_invocation(self.plan, wire_id)["arguments"],
            overridden="prompt" in (self.invocation_arguments or {}),
        )
        return arguments, prompt

    def to_effect(
        self, batch: lineage_program_packet_routing.PacketWireBatch, packet: dict[str, Any],
        arguments: dict[str, Any], prompt: str,
    ) -> dict[str, Any]:
        from . import lineage_program_effect_execution

        invocation = _make_program_effect_invocation(
            self.event,
            source_kind="packet",
            source_id=self.source_node_id,
            source_wire_id=batch.wire_id,
            prompt=prompt,
            argument_overrides=arguments,
            packet=packet,
            from_resident_id=self.source_node_id if self.source_node_id.startswith("resident_") else "",
            include_envelope=_bool(batch.config.get("include_envelope"), True),
            argument_mapping=_program_compiled_invocation(self.plan, batch.wire_id)["arguments"],
        )
        return lineage_program_effect_execution._execute_program_effect_component(
            self.lineage, self.plan, batch.target_id, invocation, self.actor,
            host=self.ports.flow.effect_authority(),
        )

    def to_resident(
        self, batch: lineage_program_packet_routing.PacketWireBatch, packet: dict[str, Any], prompt: str,
    ) -> dict[str, Any]:
        """Deliver now when the Resident can take it; otherwise drop at the message limit or defer."""

        if not batch.target_node:
            raise ValueError("Packet Invoke 的下游已不存在")
        packet_invocation = {
            "source_kind": "packet",
            "source_id": self.source_node_id,
            "source_wire_id": batch.wire_id,
            "prompt": prompt,
            "packet": packet,
            "include_envelope": _bool(batch.config.get("include_envelope"), True),
        }
        packet_route = {
            "wire_id": batch.wire_id,
            "target_id": batch.target_id,
            "target_kind": "agent",
            "config": batch.config,
        }
        can_consume, reason = program_event_route_can_consume(
            self.lineage_id, self.lineage, packet_route, self.actor,
            authority=self.ports.flow.event_authority(),
        )
        budget = wire_delivery_budget.views(str(self.lineage["lineage_id"]), {batch.wire_id: batch.config})[batch.wire_id]
        if can_consume:
            return _dispatch_program_packet_to_agent(
                self.lineage, batch.target_id, self.event, packet_invocation, self.actor, ports=self.ports,
            )
        if budget["message_limit_reached"]:
            return {"status": "dropped", "reason": "message_limit_reached", "target_id": batch.target_id}
        return _defer_program_event_route(
            self.lineage_id,
            self.source_node_id,
            packet_route,
            self.event,
            f"packet:{self.event.id}:{self.source_node_id}:{batch.wire_id}:{packet.get('id')}",
            reason=reason,
            invocation=packet_invocation,
        )

    def to_invoke_target(self, batch: lineage_program_packet_routing.PacketWireBatch, packet: dict[str, Any]) -> dict[str, Any]:
        arguments, prompt = self.invocation_input(batch.wire_id, packet)
        if batch.target_kind == "effect":
            return self.to_effect(batch, packet, arguments, prompt)
        return self.to_resident(batch, packet, prompt)


def dispatch_program_packets(
    lineage: dict[str, Any],
    plan: dict[str, Any],
    source_node_id: str,
    event: lineage_primitives.LineageEvent,
    packets: list[lineage_primitives.Packet | dict[str, Any]],
    actor: str,
    *,
    packet_wires: Iterable[dict[str, Any]],
    packets_by_source_port: dict[
        str,
        list[lineage_primitives.Packet | dict[str, Any]],
    ]
    | None = None,
    state: str = "continue",
    invocation_arguments: dict[str, Any] | None = None,
    prepare_barriers: bool = False,
    ports: ProgramExecutionPorts,
) -> dict[str, Any]:
    """Route a Packet collection through the caller's compiled scope."""
    packet_wires = list(packet_wires)
    dispatch = _PacketDispatch(lineage, plan, source_node_id, event, actor, state, invocation_arguments, ports)
    if prepare_barriers:
        # Resident Output and Loop rounds start occurrences independently of
        # Event-source dispatch. Freeze the entire operator graph before the
        # first branch can run; nested operators reuse the same barriers.
        prepare_operator_inboxes(
            dispatch.lineage_id,
            event,
            _program_operator_barriers(
                lineage, source_node_id,
                route_ids=[str(wire["id"]) for wire in packet_wires],
            ),
        )
    deliveries: list[dict[str, Any]] = []
    operator_results: list[dict[str, Any]] = []
    routed_packet_count = (
        sum(len(items) for items in packets_by_source_port.values())
        if packets_by_source_port is not None
        else len(packets)
    )
    for batch in lineage_program_packet_routing.iter_packet_batches(
        lineage,
        plan,
        source_node_id,
        event,
        packets,
        packet_wires,
        packets_by_source_port,
    ):
        if batch.target_kind == "operator":
            operator_results.append(dispatch.to_operator(batch))
            continue
        for packet in batch.packets:
            delivery = (
                dispatch.to_loop(batch, packet)
                if batch.target_kind == "loop"
                else dispatch.to_invoke_target(batch, packet)
            )
            deliveries.append({"wire_id": batch.wire_id, "packet_id": str(packet.get("id") or ""), **delivery})
    return {
        "deliveries": deliveries,
        "operators": operator_results,
        "packet_count": routed_packet_count,
    }


def _dispatch_program_packet_to_agent(
    lineage: dict[str, Any],
    target_id: str,
    event: lineage_primitives.LineageEvent,
    invocation: dict[str, Any],
    actor: str,
    *, ports: ProgramExecutionPorts,
) -> dict[str, Any]:
    from contextlib import contextmanager
    from management_common.operation_errors import OperationError
    from . import resident_intake_admission

    lineage_id = str(lineage["lineage_id"])
    current = ports.state.read_lineage(lineage_id, actor=actor)
    wire = next((item for item in _compiled_program(current)["wires"]
                 if item["id"] == invocation.get("source_wire_id")), None)
    if not wire or wire["target"] != target_id:
        return {"status": "dropped", "reason": "route_changed", "target_id": target_id}

    @contextmanager
    def admission():
        with ports.state.lineage_execution_authority(lineage_id):
            latest = ports.state.read_lineage(lineage_id, actor=actor)
            latest_wire = next((item for item in _compiled_program(latest)["wires"] if item["id"] == wire["id"]), None)
            if latest_wire != wire:
                raise OperationError('Lineage route changed before message admission',
                                     code='instruction.conflict', http_status=409, commit_state='not_committed', retryable=False)
            yield

    packet = invocation.get('packet') or {}
    message_id = lineage_program_identity.delivery_message_id(
        lineage_id, event.id, str(wire['id']), target_id, packet_id=str(packet.get('id') or ''),
    )
    def accepted(identity):
        wire_delivery_budget.accept(lineage_id, wire['id'], message_id, identity)
        ports.resident.register_program_agent_invocation(
            lineage_id, target_id, identity, event, wire['id'], actor,
        )

    with resident_intake_admission.bind(target_id, message_id, admission, accepted) as binding:
        return _deliver_packet_to_agent(current, target_id, event, invocation, actor, ports=ports, admission=binding)


def _deliver_packet_to_agent(
    lineage: dict[str, Any], target_id: str, event: lineage_primitives.LineageEvent,
    invocation: dict[str, Any], actor: str, *, ports: ProgramExecutionPorts, admission,
) -> dict[str, Any]:
    target = next(
        (
            node
            for node in lineage.get("nodes") or []
            if isinstance(node, dict)
            and str(node.get("id") or "") == target_id
        ),
        None,
    )
    if not target:
        raise ValueError("Packet Invoke 的普通 Agent 已不存在")
    packet = (
        invocation.get("packet")
        if isinstance(invocation.get("packet"), dict)
        else {}
    )
    immediate_source_id = str(invocation.get("source_id") or "")
    source_node_id = str(
        packet.get("from_node_id") or immediate_source_id
    )
    source_resident_id = str(packet.get("from_resident_id") or "")
    prompt = lineage_program_packet_routing.packet_invoke_prompt(
        event,
        source_node_id=source_node_id,
        source_resident_id=source_resident_id,
        routed_by_node_id=immediate_source_id,
        prompt=lineage_primitives.normalize_invocation_prompt(invocation.get("prompt")),
        packet_id=str(packet.get("id") or ""),
        packet_key=str(packet.get("key") or ""),
        packet=packet,
        include_envelope=_bool(
            invocation.get("include_envelope"),
            True,
        ),
    )
    if not prompt:
        raise ValueError("Packet Invoke 缺少 Prompt")
    source_wire_id = str(invocation.get("source_wire_id") or "")
    delivery_message_id = lineage_program_identity.delivery_message_id(
        str(lineage.get("lineage_id") or ""),
        event.id,
        source_wire_id,
        target_id,
        packet_id=str(packet.get("id") or ""),
    )
    from .family_familize import facade_speaking_to
    from .lineage_event_dispatch_rules import _program_event_delivery_profile
    wire = next((wire for wire in _compiled_program(lineage)["wires"] if wire["id"] == source_wire_id), {})
    lineage_id = str(lineage["lineage_id"])
    receipt = wire_delivery_budget.reserve(
        lineage_id, source_wire_id, delivery_message_id,
        normalize_optional_limit((wire.get("config") or {}).get("max_messages")),
    )
    if receipt["status"] == "limited":
        return {"status": "dropped", "reason": "message_limit_reached", "target_id": target_id}
    profile = _program_event_delivery_profile(event, {"config": wire.get("config") or {}})
    if profile:
        lineage_program_resident_inputs.record_delivery_profile(str(lineage["lineage_id"]), delivery_message_id, profile)
    facade = facade_speaking_to(lineage, target_id)
    expected_execution_id = receipt.get("accepted_message_id")
    if receipt["status"] != "sent":
        try:
            response = ports.resident.send_node_message(
                target, prompt, facade or actor,
                message_id=delivery_message_id,
                input_media=(packet.get("message") or {}).get("content_parts"),
                instruction_context=lineage_instruction_context.resident_context(
                    lineage_id, event, actor, target_id, sender=facade,
                ),
            )
        except Exception as exc:
            if getattr(exc, "commit_state", "unknown") == "not_committed":
                wire_delivery_budget.release(lineage_id, source_wire_id, delivery_message_id)
            raise
        expected_execution_id = resident_message_contract.delivered_message_id(response)
        if not expected_execution_id:
            raise RuntimeError("Packet 已发送，但无法确认目标 Resident job")
    if not admission.accepted:
        # A durable ISA replay can return without re-entering intake. Restore
        # its handoff while serialized with graph edits, using the same ID.
        with admission.guard():
            admission.accept(expected_execution_id)
            admission.accepted = True
    return {
        "status": "sent",
        "target_id": target_id,
        "target_kind": "agent",
        "message_id": expected_execution_id,
    }

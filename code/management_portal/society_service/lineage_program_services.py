"""Composition of Program delivery, polling and recovery capabilities.

The host owns aggregate access, authorization, locks and public entry points.
This module assembles the explicit capabilities consumed by Program policies;
it does not own delivery or settlement rules. Gateways resolve host callbacks
when called, so replaced domain functions remain visible to already-built ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from . import (
    lineage_catalog,
    lineage_event_reconciliation,
    lineage_primitives,
    lineage_program_backlog,
    lineage_program_effect_execution,
    lineage_program_event_dispatch,
    lineage_program_execution,
    lineage_program_polling,
    lineage_program_terminal_reconciliation,
)


class ResidentEvidence(Protocol):
    def get_latest_completed_message_reference(self, resident_id: str, actor: str) -> dict[str, Any]: ...


class ProgramServiceHost(Protocol):
    """Explicit callback surface supplied by the Lineage composition root.

    Callback signatures are constrained by the consumer ports and gateways.
    No state is copied and no fallback attribute forwarding is provided.
    """

    resident_protocol: ResidentEvidence
    _lineage_execution_authority: Callable[..., Any]
    _lineage_scope: Callable[..., Any]
    _read_lineage: Callable[..., Any]
    _read_lineage_state: Callable[..., Any]
    get_lineage: Callable[..., Any]
    _ensure_manager: Callable[..., Any]
    _persist_lineage_runtime_state: Callable[..., Any]
    _now_iso: Callable[..., Any]
    _resume_program_execution_principal: Callable[..., Any]
    _send_node_message: Callable[..., Any]
    _node_has_active_execution: Callable[..., Any]
    _register_program_agent_invocation: Callable[..., Any]
    _notify_program_resident_invocation_terminal: Callable[..., Any]
    _completed_message_evidence: Callable[..., Any]
    dispatch_lineage_program_event: Callable[..., Any]
    _program_control_authority: Callable[..., Any]
    _program_execution_ports: Callable[..., Any]
    _program_event_authority: Callable[..., Any]
    _lineage_snapshot_for_read: Callable[..., Any]
    _pending_event_has_registered_invocation: Callable[..., Any]
    _retire_exhausted_event_sources: Callable[..., Any]
    observe_trigger_path: Callable[..., Any]
    _node_resident_id: Callable[..., Any]
    handle_resident_message_completion: Callable[..., Any]
    _resident_input_reconciliation_state: Callable[..., Any]
    handle_resident_message_terminal: Callable[..., Any]
    _actor_can_manage_lineage: Callable[..., Any]
    _lineage_scopes: Callable[..., Any]
    _lineage_catalog_store: Callable[..., Any]
    _lineage_runtime_actor: Callable[..., Any]
    _read_resident_activity_snapshot: Callable[..., Any]
    reconcile_lineage_system_events: Callable[..., Any]
    reconcile_lineage_runtime: Callable[..., Any]
    _clone_json: Callable[..., Any]
    _lineage_node_session_key: Callable[..., Any]
    _program_event_source_contract_fingerprint: Callable[..., Any]
    _program_resident_state_value: Callable[..., Any]
    _role_file_signature: Callable[..., Any]
    _scheduled_time_reached: Callable[..., Any]


@dataclass(frozen=True)
class _BoundHost:
    _host: ProgramServiceHost


def execution_ports(host: ProgramServiceHost) -> lineage_program_execution.ProgramExecutionPorts:
    return lineage_program_execution.ProgramExecutionPorts(
        state=_LineageStateGateway(host),
        resident=_ResidentDeliveryGateway(host),
        flow=_ProgramFlowGateway(host),
    )


def polling_ports(host: ProgramServiceHost) -> lineage_program_polling.ProgramPollingPorts:
    return lineage_program_polling.ProgramPollingPorts(
        lineage_catalog_store=host._lineage_catalog_store,
        lineage_runtime_actor=host._lineage_runtime_actor,
        read_lineage_state=host._read_lineage_state,
        read_resident_activity_snapshot=host._read_resident_activity_snapshot,
        ensure_manager=host._ensure_manager,
        reconcile_lineage_system_events=host.reconcile_lineage_system_events,
        reconcile_lineage_runtime=host.reconcile_lineage_runtime,
    )


def terminal_ports(host: ProgramServiceHost) -> lineage_program_terminal_reconciliation.ProgramTerminalPorts:
    return lineage_program_terminal_reconciliation.ProgramTerminalPorts(
        state=_LineageStateGateway(host),
        resident=_ProgramTerminalResidentGateway(host),
        flow=_ProgramFlowGateway(host),
        directory=_ProgramTerminalDirectoryGateway(host),
    )


def event_authority(host: ProgramServiceHost) -> lineage_program_event_dispatch.ProgramEventAuthority:
    return _ProgramEventAuthority(host)


def control_authority(host: ProgramServiceHost) -> lineage_program_backlog.ProgramControlAuthority:
    return _ProgramControlAuthority(host)


def event_reconcile_ports(host: ProgramServiceHost) -> lineage_event_reconciliation.EventReconcilePorts:
    return lineage_event_reconciliation.EventReconcilePorts(
        clone_json=host._clone_json,
        ensure_manager=host._ensure_manager,
        lineage_node_session_key=host._lineage_node_session_key,
        event_contract_fingerprint=host._program_event_source_contract_fingerprint,
        resident_state_value=host._program_resident_state_value,
        read_lineage=host._read_lineage,
        read_resident_activity_snapshot=host._read_resident_activity_snapshot,
        role_file_signature=host._role_file_signature,
        scheduled_time_reached=host._scheduled_time_reached,
        lineage_scope=host._lineage_scope,
        dispatch_program_event=host.dispatch_lineage_program_event,
    )


class _LineageStateGateway(_BoundHost):
    """Reads, ownership, scopes and commits of the sole Lineage aggregate."""

    def lineage_execution_authority(self, lineage_id: str):
        return self._host._lineage_execution_authority(lineage_id)

    def lineage_scope(self, lineage_id: str, *, operation: str = ""):
        return self._host._lineage_scope(lineage_id, operation=operation)

    def read_lineage(self, lineage_id: str, *, actor: str) -> dict[str, Any]:
        return self._host._read_lineage(lineage_id, actor=actor)

    def read_lineage_state(self, lineage_id: str) -> dict[str, Any]:
        return self._host._read_lineage_state(lineage_id)

    def get_lineage(self, lineage_id: str, actor: str) -> dict[str, Any]:
        return self._host.get_lineage(lineage_id, actor)

    def ensure_manager(self, lineage: dict[str, Any], actor: str) -> None:
        self._host._ensure_manager(lineage, actor)

    def persist_lineage_runtime_state(self, previous: dict[str, Any], lineage: dict[str, Any], reason: str) -> None:
        self._host._persist_lineage_runtime_state(previous, lineage, reason)

    def now_iso(self) -> str:
        return self._host._now_iso()

    def resume_program_execution_principal(self, lineage_id: str, event: lineage_primitives.LineageEvent, resident_id: str) -> str:
        return self._host._resume_program_execution_principal(lineage_id, event, resident_id)


class _ResidentDeliveryGateway(_BoundHost):
    """Resident delivery evidence and committed invocation links."""

    def send_node_message(self, node: dict[str, Any], prompt: str, actor: str, **options: Any) -> object:
        return self._host._send_node_message(node, prompt, actor, **options)

    def node_has_active_execution(self, node: dict[str, Any], actor: str) -> bool:
        return self._host._node_has_active_execution(node, actor)

    def register_program_agent_invocation(self, lineage_id: str, target_id: str, message_id: str, event: lineage_primitives.LineageEvent, source_wire_id: str, actor: str) -> dict[str, Any]:
        return self._host._register_program_agent_invocation(lineage_id, target_id, message_id, event, source_wire_id, actor)

    def notify_program_resident_invocation_terminal(self, lineage_id: str, resident_id: str, message_id: str, invocations: list[dict[str, Any]], *, status: str) -> bool:
        return self._host._notify_program_resident_invocation_terminal(lineage_id, resident_id, message_id, invocations, status=status)

    def completed_message_evidence(self, node: dict[str, Any], message_id: str, actor: str) -> dict[str, Any]:
        return self._host._completed_message_evidence(node, message_id, actor)


class _ProgramFlowGateway(_BoundHost):
    """Event and Effect dispatch capabilities."""

    def dispatch_lineage_program_event(self, *args: Any, **options: Any) -> dict[str, Any]:
        return self._host.dispatch_lineage_program_event(*args, **options)

    def drain_program_event_wire_backlog(self, lineage_id: str, actor: str, *, max_events: int = 32) -> dict[str, Any]:
        return lineage_program_backlog.drain_event_backlog(
            lineage_id, actor, max_events=max_events, authority=self._host._program_control_authority(),
        )

    def program_execution_ports(self) -> lineage_program_execution.ProgramExecutionPorts:
        return self._host._program_execution_ports()

    def event_authority(self) -> lineage_program_event_dispatch.ProgramEventAuthority:
        return self._host._program_event_authority()

    def effect_authority(self) -> lineage_program_effect_execution.ProgramEffectAuthority:
        return self._host._program_event_authority()


class _ProgramEventAuthority(_LineageStateGateway, _ResidentDeliveryGateway, _ProgramFlowGateway):
    """Everything Event dispatch and Effects may do to the sole Lineage aggregate."""

    def read_authorized(self, lineage_id: str, actor: str) -> dict[str, Any]:
        return self._host._lineage_snapshot_for_read(lineage_id, actor)


class _ProgramControlAuthority(_ProgramEventAuthority):
    """Event authority plus the backlog's invocation and run controls."""

    def has_registered_invocation(self, lineage_id: str, wire_id: str, target_id: str, occurrence: dict[str, Any]) -> bool:
        return self._host._pending_event_has_registered_invocation(lineage_id, wire_id, target_id, occurrence)

    def retire_exhausted_sources(self, lineage_id: str, actor: str) -> list[str]:
        return self._host._retire_exhausted_event_sources(lineage_id, actor)

    def observe_trigger_path(self, lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        return self._host.observe_trigger_path(lineage_id, payload, actor)


class _ProgramTerminalResidentGateway(_ResidentDeliveryGateway):
    """Evidence and completion operations owned by Resident or its invocation link."""

    def latest_completed_message_reference(self, resident_id: str, actor: str) -> dict[str, Any]:
        return self._host.resident_protocol.get_latest_completed_message_reference(resident_id, actor)

    def node_resident_id(self, node: dict[str, Any]) -> str:
        return self._host._node_resident_id(node)

    def handle_resident_message_completion(self, resident_id: str, actor: str, message_id: str, **options: Any) -> dict[str, Any]:
        return self._host.handle_resident_message_completion(resident_id, actor, message_id, **options)

    def resident_input_reconciliation_state(self, node: dict[str, Any], message_id: str, actor: str) -> dict[str, Any]:
        return self._host._resident_input_reconciliation_state(node, message_id, actor)

    def handle_resident_message_terminal(self, resident_id: str, actor: str, message_id: str, **options: Any) -> dict[str, Any]:
        return self._host.handle_resident_message_terminal(resident_id, actor, message_id, **options)


class _ProgramTerminalDirectoryGateway(_BoundHost):
    """Cross-lineage lookup, ownership and ordered multi-lineage locks."""

    def actor_can_manage_lineage(self, lineage: dict[str, Any], actor: str) -> bool:
        return self._host._actor_can_manage_lineage(lineage, actor)

    def lineage_scopes(self, lineage_ids: Iterable[str], *, operation: str = ""):
        return self._host._lineage_scopes(lineage_ids, operation=operation)

    def lineage_ids_for_resident(self, resident_id: str) -> list[str]:
        return self._host._lineage_catalog_store().lineage_ids_for_resident(resident_id)

    def lineage_catalog_store(self) -> lineage_catalog.LineageCatalog:
        return self._host._lineage_catalog_store()

    def lineage_execution_authority(self, lineage_id: str):
        return self._host._lineage_execution_authority(lineage_id)

    def lineage_runtime_actor(self, lineage: dict[str, Any]) -> str:
        return self._host._lineage_runtime_actor(lineage)

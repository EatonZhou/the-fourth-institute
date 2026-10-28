"""Program worker polling and automation principal selection."""
from __future__ import annotations
from . import business_fact_views

from dataclasses import dataclass
from typing import Any, Callable

from management_common.service_shutdown import shutdown_requested

from . import lineage_instruction_context, lineage_program_runtime
from .domain_ports import resource_port
from .lineage_program_queries import _compiled_program
from .lineage_values import _clone_json
from .lineage_automation_identity import AUTOMATIC_EVENT_TYPES, AutomationConfigurationError

@dataclass(frozen=True)
class ProgramPollingPorts:
    lineage_catalog_store: Callable[..., Any]
    lineage_runtime_actor: Callable[..., Any]
    read_lineage_state: Callable[..., Any]
    read_resident_activity_snapshot: Callable[..., Any]
    ensure_manager: Callable[..., Any]
    reconcile_lineage_system_events: Callable[..., Any]
    reconcile_lineage_runtime: Callable[..., Any]


def _has_automatic_sources(lineage: dict[str, Any], runtime: dict[str, Any] | None = None) -> bool:
    sources = (runtime or {}).get("event_sources") or {}
    return any(
        isinstance(component, dict)
        and str((component.get("config") or component).get("event_type") or "")
        in AUTOMATIC_EVENT_TYPES
        and not sources.get(component.get("id"), {}).get("paused")
        for component in (lineage.get("program") or {}).get("components") or []
    )


def recovery_lineage_ids(*, include_observations: bool) -> list[str]:
    from . import graph_storage, program_work_storage, relation_work_storage, trigger_path_storage
    from .state_store import get_lineage_data_store
    with get_lineage_data_store().database.read() as connection:
        ids = program_work_storage.recovery_lineage_ids(connection)
        ids.update(trigger_path_storage.recovery_lineage_ids(connection))
        ids.update(relation_work_storage.recovery_lineage_ids(connection))
        if include_observations:
            ids.update(graph_storage.observation_lineage_ids(connection, AUTOMATIC_EVENT_TYPES))
    return sorted(ids)


def _pending_recovery_events(runtime: dict[str, Any], handoffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only persisted activations may authorize recovery without new automation."""
    pending = [
        *handoffs,
        *runtime.get("expired_deliveries", []),
        *(item for wire in runtime.get("event_wires", {}).values()
          for item in wire.get("pending_events", [])),
        *(item for operator in runtime.get("operators", {}).values()
          for item in operator.get("pending_deliveries", {}).values()),
    ]
    return [item.get("event") or {} for item in pending if not item.get("recovery_blocked")]


def poll_all_system_events(
    *,
    ports: ProgramPollingPorts,
    recovery_only: bool = False,
) -> dict[str, Any]:
    """Recover persisted work first; new observations need explicit live authority."""
    from management_common.principal import OperationIdentity
    from . import lineage_primitives

    checked = emitted = recovered = failed = blocked = 0
    failure_samples: list[dict[str, str]] = []
    retry_lineage_ids: list[str] = []
    blocked_lineages: dict[str, str] = {}
    wake_lineages: dict[str, str] = {}
    stopped = False
    resident_activity_snapshot: dict[str, Any] | None = None

    def failure(lineage_id: str, exc: Exception) -> None:
        nonlocal failed, blocked
        if isinstance(exc, AutomationConfigurationError):
            blocked += 1
            blocked_lineages[lineage_id] = str(exc)[:500]
            return
        failed += 1
        if lineage_id not in retry_lineage_ids:
            retry_lineage_ids.append(lineage_id)
        if len(failure_samples) < 12:
            failure_samples.append({
                "lineage_id": lineage_id, "error_type": type(exc).__name__, "error": str(exc)[:500],
            })

    errors = (KeyError, PermissionError, OSError, RuntimeError, ValueError)
    for lineage_id in recovery_lineage_ids(include_observations=not recovery_only):
        if shutdown_requested():
            stopped = True
            break
        try:
            lineage = ports.lineage_catalog_store().get(lineage_id)
        except KeyError:
            continue
        except errors as exc:
            failure(lineage_id, exc)
            continue
        checked += 1
        try:
            runtime = lineage_program_runtime.read_runtime(lineage_id)
            handoffs = lineage_program_runtime.pending_resident_handoffs(lineage_id)
        except errors as exc:
            failure(lineage_id, exc)
            continue

        # A missing/changed automation configuration must not replace or block
        # an already accepted activation's identity.
        from . import lineage_recovery_policy, lineage_event_delivery, trigger_paths
        from .state_store import get_lineage_data_store
        pending_events = _pending_recovery_events(runtime, handoffs)
        if any(lineage_recovery_policy.permanent_error(e) for e in pending_events):
            try:
                blocked += lineage_event_delivery.block_unrecoverable_events(get_lineage_data_store().database, lineage_id)
                blocked += trigger_paths.block_unrecoverable_inputs(lineage_id, handoffs)
            except errors as exc:
                failure(lineage_id, exc)
                continue
        seen: set[str] = set()
        # Each identity settles only its own handoffs, but every queued Event
        # is delivered under the identity it saved; one drain that finishes
        # covers the Lineage for this pass.
        drained = False
        for raw_event in pending_events:
            if lineage_recovery_policy.permanent_error(raw_event):
                continue
            try:
                event = lineage_primitives.normalize_event(raw_event)
                if not event.identity:
                    raise PermissionError("Pending Event has no verified identity; explicit reissue is required")
                identity = OperationIdentity.from_dict(event.identity)
                key = str(identity.to_dict())
                if key in seen:
                    continue
                seen.add(key)
                with lineage_instruction_context.event_scope(event, identity.principal.id):
                    recovery = ports.reconcile_lineage_runtime(
                        lineage_id, identity.principal.id, recovery_only=True, drain=not drained,
                    )
                drained = drained or bool((recovery.get("program_event_backlog") or {}).get("complete"))
                recovered += int(recovery.get("checked_node_count") or 0)
                emitted += int(recovery.get("program_created_count") or 0)
                for item in recovery.get("recovery_failures") or []:
                    failure(lineage_id, PermissionError(str(item.get("error") or "Recovery denied")))
            except errors as exc:
                failure(lineage_id, exc)

        has_automatic = not recovery_only and _has_automatic_sources(lineage, runtime)
        has_absorption = any(
            edge.get("type") == "absorption" and (edge.get("absorption") or {}).get("target_message_id")
            for edge in lineage.get("edges") or [] if isinstance(edge, dict)
        )
        if not (has_automatic or has_absorption):
            continue
        try:
            actor = ports.lineage_runtime_actor(lineage)
            with lineage_instruction_context.automatic_scope(actor):
                has_all_idle = any(
                    (component.get("config") or component).get("event_type") == "residents.all_idle"
                    for component in (lineage.get("program") or {}).get("components") or []
                    if isinstance(component, dict)
                )
                if has_automatic and has_all_idle and resident_activity_snapshot is None:
                    resident_activity_snapshot = ports.read_resident_activity_snapshot(None)
                result = ports.reconcile_lineage_system_events(
                    lineage_id, actor,
                    resident_activity_snapshot=resident_activity_snapshot if has_all_idle else None,
                ) if has_automatic else {}
                if int(result.get("retry_after_seconds") or 0) > 0 and lineage_id not in retry_lineage_ids:
                    retry_lineage_ids.append(lineage_id)
                if result.get("next_wake_at"):
                    wake_lineages[lineage_id] = str(result["next_wake_at"])
                emitted += int(result.get("event_count") or 0)

                current = business_fact_views.canonical_view(
                    _clone_json(ports.read_lineage_state(lineage_id)), resource_id=lineage_id, actor=actor,
                )
                ports.ensure_manager(current, actor)
                plan = _compiled_program(current)
                has_completion_source = any(
                    source.get("event_type") == "resident.work_finished" and source.get("subscriber_routes")
                    for source in (plan.get("event_sources") or {}).values()
                )
                has_absorption = any(
                    edge.get("type") == "absorption" and (edge.get("absorption") or {}).get("target_message_id")
                    for edge in current.get("edges") or [] if isinstance(edge, dict)
                )
                if has_completion_source or has_absorption:
                    recovery = ports.reconcile_lineage_runtime(lineage_id, actor)
                    recovered += int(recovery.get("checked_node_count") or 0)
                    emitted += int(recovery.get("program_created_count") or 0)
        except errors as exc:
            failure(lineage_id, exc)
    return {
        "checked": checked, "emitted": emitted, "recovered": recovered, "failed": failed, "blocked": blocked,
        "failure_samples": failure_samples, "retry_lineage_ids": retry_lineage_ids,
        "blocked_lineages": blocked_lineages,
        "wake_lineages": wake_lineages, "stopped": stopped,
    }


def _runtime_lineage(clean_id: str, *, ports: ProgramPollingPorts) -> tuple[dict[str, Any] | None, str]:
    """Resolve an active definition for the internal scheduler service."""
    try:
        definition = ports.lineage_catalog_store().get(clean_id)
    except KeyError:
        return None, "missing"
    if definition.get("deleted_at"):
        return None, "missing"
    return definition, ""


def reconcile_system_events_as_service(
    lineage_id: str,
    *,
    ports: ProgramPollingPorts,
) -> dict[str, Any]:
    """Reconcile observations with the Lineage's current Leader identity."""

    clean_id = str(lineage_id or "").strip()
    lineage, status = _runtime_lineage(clean_id, ports=ports)
    if lineage is None:
        return {"lineage_id": clean_id, "status": status, "checked": 0}
    if not _has_automatic_sources(lineage, lineage_program_runtime.read_runtime(clean_id)):
        return {"lineage_id": clean_id, "event_count": 0, "events": []}
    try:
        actor = ports.lineage_runtime_actor(lineage)
        with lineage_instruction_context.automatic_scope(actor):
            return ports.reconcile_lineage_system_events(clean_id, actor)
    except AutomationConfigurationError as exc:
        return {"lineage_id": clean_id, "status": "blocked", "error": str(exc), "event_count": 0, "events": []}
    except KeyError:
        if _runtime_lineage(clean_id, ports=ports)[0] is None:
            return {"lineage_id": clean_id, "status": "missing", "checked": 0}
        raise


def dispatch_arrival_as_service(
    lineage_id: str,
    resident_id: str,
    message_id: str,
    *,
    ports: ProgramPollingPorts,
    dispatch: Callable[..., dict[str, Any]],
    contract: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Produce ``resident.message_received`` for one accepted message as the Portal scheduler service."""

    from . import lineage_message_events

    clean_id = str(lineage_id or "").strip()
    lineage, status = _runtime_lineage(clean_id, ports=ports)
    if lineage is None:
        return {"lineage_id": clean_id, "status": status, "events": []}
    plan = _compiled_program(ports.read_lineage_state(clean_id))
    if not lineage_message_events.message_received_sources(plan):
        return {"lineage_id": clean_id, "status": "none", "events": []}
    actor = ports.lineage_runtime_actor(lineage)

    def fire(source_id: str, event: Any, cursor: str, source: dict[str, Any]) -> dict[str, Any]:
        return dispatch(clean_id, source_id, event, actor, cursor=cursor,
                        expected_event_source_contract=contract(source))

    with lineage_instruction_context.automatic_scope(actor):
        events = lineage_message_events.dispatch_arrival(plan, actor, resident_id, message_id, fire)
    return {"lineage_id": clean_id, "status": "dispatched" if events else "none", "events": events}


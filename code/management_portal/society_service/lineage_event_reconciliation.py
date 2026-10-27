"""Recognize system facts and dispatch Program Events.

The Lineage entry owns actor authorization and IO callbacks. This module runs
one reconciliation pass in three steps:

1. read the facts other subsystems own (Resident activity, messages, Graph
   State, context usage, release publications) outside the Lineage graph lock;
2. under the lock, give each Event Source to the observer of its event type
   (``lineage_event_observers.OBSERVERS``), which records baselines and lists
   candidate Events;
3. dispatch the candidates and move the baseline of each accepted one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import (
    lineage_graph_state, lineage_message_events, lineage_primitives, lineage_program, lineage_program_runtime,
    lineage_release_events, trigger_paths,
)
from .domain_ports import resident_domain_port as resident_domain
from .lineage_values import _safe_actor
from .lineage_program_queries import _compiled_program
from .lineage_event_conditions import (
    _graph_state_condition_event_sources, _resident_activity_event_sources,
    _resident_context_pressure_event_sources,
)
from .lineage_event_observers import OBSERVERS, Facts, Scan, Source


@dataclass(frozen=True)
class EventReconcilePorts:
    clone_json: Callable[..., Any]
    ensure_manager: Callable[..., Any]
    lineage_node_session_key: Callable[..., Any]
    event_contract_fingerprint: Callable[..., Any]
    resident_state_value: Callable[..., Any]
    read_lineage: Callable[..., Any]
    read_resident_activity_snapshot: Callable[..., Any]
    role_file_signature: Callable[..., Any]
    scheduled_time_reached: Callable[..., Any]
    lineage_scope: Callable[..., Any]
    dispatch_program_event: Callable[..., Any]


def reconcile_lineage_program_system_events(
    lineage_id: str,
    actor: str,
    *,
    resident_activity_snapshot: dict[str, Any] | None = None,
    ports: EventReconcilePorts,
) -> dict[str, Any]:
    """Resolve unattended authority independently of the caller asking for a poll."""
    from . import lineage_automation_identity, lineage_instruction_context

    preview = ports.read_lineage(lineage_id, actor=actor)
    from .lineage_program_polling import _has_automatic_sources
    automatic = _has_automatic_sources(preview)
    if not automatic:
        return _reconcile_lineage_program_system_events(
            lineage_id, actor, resident_activity_snapshot=resident_activity_snapshot, ports=ports,
        )
    runtime_actor = lineage_automation_identity.runtime_actor(preview)
    with lineage_instruction_context.automatic_scope(runtime_actor):
        return _reconcile_lineage_program_system_events(
            lineage_id, runtime_actor, resident_activity_snapshot=resident_activity_snapshot, ports=ports,
        )


def _reconcile_lineage_program_system_events(
    lineage_id: str,
    actor: str,
    *,
    resident_activity_snapshot: dict[str, Any] | None = None,
    ports: EventReconcilePorts,
) -> dict[str, Any]:
    """Recognize time, file, Graph State, and Resident runtime facts.

    File sources store only a locator and content signature. The first live read
    establishes a baseline; no file contents or replayable observation are
    persisted.
    """
    actor_id = _safe_actor(actor)
    facts = _read_facts(lineage_id, actor, actor_id, resident_activity_snapshot, ports)
    with ports.lineage_scope(lineage_id):
        scan = _observe(lineage_id, actor, actor_id, facts, ports)
    return _dispatch_pending(lineage_id, actor_id, scan, ports.dispatch_program_event)


def _read_facts(
    lineage_id: str,
    actor: str,
    actor_id: str,
    resident_activity: dict[str, Any] | None,
    ports: EventReconcilePorts,
) -> Facts:
    """The facts the Lineage's Event Sources need from subsystems that own their own locks.

    A look at the Lineage under its graph lock says which facts are needed;
    each fact is then read outside the lock. A caller may pass the Resident
    activity it already holds.
    """
    with ports.lineage_scope(lineage_id):
        preview = ports.read_lineage(lineage_id, actor=actor)
        ports.ensure_manager(preview, actor_id)
        plan = _compiled_program(preview)
        nodes = {
            str(node.get("id") or ""): node
            for node in preview.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "")
        }
        activity_sources = _resident_activity_event_sources(plan)
        pressure_sources = _resident_context_pressure_event_sources(plan)
        state_condition_sources = _graph_state_condition_event_sources(plan)
        message_sources = lineage_message_events.message_received_sources(plan)
        # residents.all_idle watches every Resident of Portal; the other
        # activity sources need the Lineage's Residents and those they name.
        requested_ids: list[str] | None = []
        if any(str(source.get("event_type") or "") == "residents.all_idle" for source in activity_sources):
            requested_ids = None
        elif activity_sources:
            requested_ids = sorted({
                *nodes,
                *(
                    str(value)
                    for source in activity_sources
                    for value in [*(source.get("resident_ids") or []), *lineage_program.event_source_subject_ids(source)]
                    if str(value)
                ),
            })
        pressure_session_keys = {
            ports.lineage_node_session_key(subject)
            for source in pressure_sources
            for subject in [nodes.get(subject_id) for subject_id in lineage_program.event_source_subject_ids(source)]
            if isinstance(subject, dict) and ports.lineage_node_session_key(subject)
        }
    if resident_activity is None and activity_sources:
        # Resident registry and job-file scans do not belong to the Lineage
        # graph lock. Capture them before entering Event reconciliation.
        resident_activity = ports.read_resident_activity_snapshot(requested_ids)
    if resident_activity is not None and activity_sources:
        resident_activity = _with_facade_activity(resident_activity, activity_sources)
    return Facts(
        resident_activity=resident_activity,
        # Conversation logs belong to the Resident subsystem; read them outside
        # the Lineage graph lock like the other Resident facts.
        message_arrivals=lineage_message_events.prefetch_message_arrivals(lineage_id, message_sources, actor_id),
        release_publications=lineage_release_events.prefetch(lineage_id, plan),
        # Graph State owns its own lock and persistence contract; this is its
        # narrow live projection.
        graph_state=lineage_graph_state.read_state(lineage_id) if state_condition_sources else None,
        # Context usage is owned by the Resident execution subsystem. The
        # Event runtime consumes only its actor-scoped percentage projection.
        context_pressures=(
            resident_domain.get_resident_context_pressures(sorted(pressure_session_keys), actor_id)
            if pressure_session_keys else {}
        ),
    )


def _observe(lineage_id: str, actor: str, actor_id: str, facts: Facts, ports: EventReconcilePorts) -> Scan:
    """Give each Event Source of the Lineage to its observer; the caller holds the graph lock."""
    lineage = ports.read_lineage(lineage_id, actor=actor)
    ports.ensure_manager(lineage, actor_id)
    plan = _compiled_program(lineage)
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    scan = Scan(
        lineage_id=lineage_id,
        actor_id=actor_id,
        lineage=lineage,
        nodes={str(node.get("id") or ""): node for node in lineage.get("nodes") or [] if isinstance(node, dict)},
        runtime_sources=runtime.get("event_sources") if isinstance(runtime.get("event_sources"), dict) else {},
        facts=facts,
        ports=ports,
    )
    scoped_from, scoped_subject = 0, ""
    for event_source_id, event_source, subject in _observed_sources(plan):
        _scope_pending(scan.pending, scoped_from, scoped_subject)
        scoped_from, scoped_subject = len(scan.pending), subject
        if not isinstance(event_source, dict):
            continue
        state = scan.runtime_sources.get(event_source_id) or {}
        if state.get("paused"):
            # Paused sources observe nothing; resuming re-establishes baselines.
            continue
        observer = OBSERVERS.get(str(event_source.get("event_type") or ""))
        if observer is None:
            continue
        # An exhausted source never fires again; it needs no wake.
        exhausted = lineage_program_runtime.limit_reached(
            lineage_program_runtime.normalize_optional_limit(event_source.get("max_fires")),
            max(0, int(state.get("fire_count") or 0)),
        )
        observer(scan, Source(event_source_id, event_source, subject, exhausted))
    _scope_pending(scan.pending, scoped_from, scoped_subject)
    return scan


def _dispatch_pending(lineage_id: str, actor_id: str, scan: Scan, dispatch: Callable[..., Any]) -> dict[str, Any]:
    """Dispatch the candidate Events; an accepted one moves its source's baseline."""
    events: list[dict[str, Any]] = []
    event_count = 0
    for item in scan.pending:
        result = _dispatch_observation(lineage_id, item, actor_id, dispatch)
        events.append(result)
        accepted = bool(
            int(result.get("matched_count") or 0)
            or int(result.get("consumed_count") or 0)
            or result.get("duplicate")
            or bool((result.get("fire") or {}).get("accepted"))
        )
        if int(result.get("matched_count") or 0):
            event_count += 1
        if item.get("recurring_scheduled_at") and (accepted or result.get("exhausted")):
            lineage_program_runtime.commit_recurring_schedule(
                lineage_id,
                item["event_source_id"],
                generation=int(item["recurring_generation"]),
                scheduled_at=str(item["recurring_scheduled_at"]),
            )
        if accepted and item.get("file_locator"):
            lineage_program_runtime.commit_file_signature(
                lineage_id,
                item["event_source_id"],
                locator=item["file_locator"],
                previous_signature=item["previous_signature"],
                signature=item["file_signature"],
            )
        if accepted and item.get("state_locator"):
            lineage_program_runtime.commit_event_source_value(
                lineage_id,
                item["event_source_id"],
                locator=item["state_locator"],
                previous_signature=item["previous_state_signature"],
                signature=item["state_signature"],
                value=item["state_value"],
                subject=str(item.get("state_subject") or ""),
            )
    return {
        "baseline_count": scan.baseline_count,
        "event_count": event_count,
        "events": events,
        # When a time or file source of this Lineage needs to be looked at
        # again; the Lineage coordinator wakes for it.
        "next_wake_at": _iso_wake(min(scan.wake_times)) if scan.wake_times else "",
    }


def _iso_wake(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dispatch_observation(lineage_id, item, actor, dispatch):
    """Recovery uses the accepted fact; live observations are only candidates."""
    from management_common.principal import OperationIdentity
    from . import lineage_instruction_context

    event = item["event"]
    original = trigger_paths.event_occurrence(lineage_id, event.id)
    if original:
        if original["root_node_id"] != item["event_source_id"]:
            raise ValueError("Observed occurrence belongs to a different Event Source")
        event = lineage_primitives.normalize_event(original["input"])
        if original["status"] in trigger_paths.TERMINAL:
            # The observation cursor may lag successful graph propagation.
            # Recognize that fact without re-running any business handler.
            return {"event": event.to_dict(), "event_source_id": item["event_source_id"],
                    "trigger_path_id": original["trigger_path_id"], "duplicate": True,
                    "matched_count": 0, "consumed_count": 0}
        if not event.identity:
            raise PermissionError("Accepted Event has no verified identity")
        actor = OperationIdentity.from_dict(event.identity).principal.id
        with lineage_instruction_context.event_scope(event, actor):
            return dispatch(lineage_id, item["event_source_id"], event, actor,
                            cursor=item["cursor"], expected_event_source_contract=item["source_contract"])
    return dispatch(lineage_id, item["event_source_id"], event, actor,
                    cursor=item["cursor"], expected_event_source_contract=item["source_contract"])


def _observed_sources(plan: dict[str, Any]):
    """Each Event Source once, or once per Resident when it watches several.

    The per-Resident view names that Resident as its subject, so every
    observer looks at one Resident; its contract fingerprint stays the source's.
    """

    for event_source_id, event_source in sorted((plan.get("event_sources") or {}).items()):
        if isinstance(event_source, dict) and lineage_program.watches_many_residents(event_source):
            for subject_id in lineage_program.event_source_subject_ids(event_source):
                yield event_source_id, {**event_source, "subject_resident_id": subject_id}, subject_id
            continue
        yield event_source_id, event_source, ""


def _scope_pending(pending: list[dict[str, Any]], start: int, subject: str) -> None:
    """Events read for one watched Resident commit that Resident's observation."""

    if not subject:
        return
    for item in pending[start:]:
        if item.get("state_locator") and not item.get("state_subject"):
            item["state_subject"] = subject


def _with_facade_activity(snapshot: dict[str, Any], activity_sources: list[dict[str, Any]]) -> dict[str, Any]:
    """A Family facade never runs; its work is the member work it relayed (decision_44cf587c1721)."""

    from . import family_facade

    subjects = {
        subject_id
        for source in activity_sources
        if str(source.get("event_type") or "") == "resident.work_started"
        for subject_id in lineage_program.event_source_subject_ids(source)
    }
    if not subjects:
        return snapshot
    residents = dict(snapshot.get("residents") or {})
    for subject_id in sorted(subjects):
        try:
            activity = family_facade.relay_activity(subject_id)
        except Exception:  # noqa: BLE001 - the facade's own activity stays as read
            continue
        if activity is not None:
            residents[subject_id] = {**dict(residents.get(subject_id) or {}), **activity}
    return {**snapshot, "residents": residents}

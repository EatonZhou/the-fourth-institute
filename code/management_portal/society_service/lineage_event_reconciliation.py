"""Recognize system facts and dispatch Program Events.

The Lineage entry owns actor authorization and IO callbacks. This module owns
observation, edge transitions, baseline handling and event emission policy.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import lineage_graph_state, lineage_message_events, lineage_primitives, lineage_program, lineage_program_runtime, trigger_paths
from .domain_ports import resident_domain_port as resident_domain
from .lineage_values import _safe_actor
from .lineage_program_queries import _compiled_program
from . import lineage_release_events
from .lineage_event_conditions import (
    _boolean_condition_pending_state, _commit_boolean_event_condition,
    _elapsed_whole_seconds, _graph_state_condition_event_sources,
    _observe_boolean_event_condition, _resident_activity_event_sources,
    _resident_context_pressure_event_sources, _state_condition_matches,
)


# A file source has no due time, so its Lineage is looked at again this often.
FILE_SOURCE_POLL_SECONDS = 60
# A Run whose Residents all finished longer ago than this already had its
# chance to fire residents.all_finished.  Looking at every historical Run fires
# again those whose fire cursor predates bounded trigger paths or has aged out
# of the source's last MAX_EVENT_CURSORS cursors.
RESIDENTS_FINISHED_WINDOW = timedelta(hours=24)


def _recent_resident_inputs(lineage_id: str) -> list[dict[str, Any]]:
    """Resident leaves of Runs still in progress or finished within the window."""
    cutoff = datetime.now(timezone.utc) - RESIDENTS_FINISHED_WINDOW
    items = trigger_paths.resident_inputs(lineage_id, finished_since=cutoff)
    settled: dict[str, bool] = {}
    for item in items:
        path_id = str(item.get("trigger_path_id") or "")
        completed_at = str(item.get("completed_at") or "")
        old = bool(completed_at) and datetime.fromisoformat(completed_at.replace("Z", "+00:00")) < cutoff
        settled[path_id] = settled.get(path_id, True) and old
    return [item for item in items if not settled.get(str(item.get("trigger_path_id") or ""), False)]


def _note_wake(wake_times: list[datetime], value: object) -> None:
    """Remember a future due time; a due time already passed is handled now or by retries."""

    text = str(value or "").strip()
    if not text:
        return
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    # Schedules are computed on the runtime's clock; compare on the same one.
    if moment > lineage_program_runtime.datetime.now(timezone.utc):
        wake_times.append(moment)


def _iso_wake(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def _all_resident_activity_member_ids(
    activity_snapshot: dict[str, Any],
) -> list[str]:
    """Return every Resident represented by the canonical global snapshot."""

    return sorted({
        str(value)
        for value in activity_snapshot.get("resident_ids") or []
        if str(value)
    })


HANDOFF_EVENT_STATUSES = {
    "workspace_handoff.ready": frozenset({"ready"}),
    "workspace_handoff.failed": frozenset({"failed"}),
}


def _observed_value(
    lineage_id: str,
    event_type: str,
    event_source: dict[str, Any],
    lineage: dict[str, Any],
    graph_state_snapshot: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], frozenset[str] | None] | None:
    """Current value of a value-observing source: (value, event payload, reported values).

    ``lineage.changed`` observes the Lineage version, ``state.changed`` one
    shared state key and ``workspace_handoff.*`` the status of one handoff.
    Values stay short: the runtime keeps only a locator, a signature and the value.
    """

    if event_type == "lineage.changed":
        revision = int(lineage.get("revision") or 0)
        return str(revision), {"revision": revision, "locator": lineage_id}, None
    if event_type == "state.changed":
        if graph_state_snapshot is None:
            return None
        key = str(event_source.get("key") or "")
        cells = graph_state_snapshot.get("cells") if isinstance(graph_state_snapshot.get("cells"), dict) else {}
        if key in cells:
            encoded = json.dumps(cells[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            value = "present:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
        else:
            value = "absent"
        return value, {
            "key": key,
            "state_revision": max(0, int(graph_state_snapshot.get("revision") or 0)),
            "locator": key,
        }, None
    if event_type in HANDOFF_EVENT_STATUSES:
        handoff_id = str(event_source.get("subject_handoff_id") or "")
        handoff = next(
            (
                edge for edge in lineage.get("edges") or []
                if isinstance(edge, dict) and edge.get("type") == "handoff" and str(edge.get("id") or "") == handoff_id
            ),
            None,
        )
        if not handoff_id or handoff is None:
            return None
        attempts = max(
            [len(item.get("attempts") or []) for item in handoff.get("items") or [] if isinstance(item, dict)] or [0]
        )
        status = str(handoff.get("status") or "draft")
        payload = {"handoff_id": handoff_id, "status": status, "locator": handoff_id}
        if status == "failed" and handoff.get("error"):
            payload["diagnostic"] = str(handoff.get("error") or "")[:1000]
        return f"{status}:{attempts}", payload, HANDOFF_EVENT_STATUSES[event_type]
    return None


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

    _clone_json = ports.clone_json
    _ensure_manager = ports.ensure_manager
    _lineage_node_session_key = ports.lineage_node_session_key
    _program_event_source_contract_fingerprint = ports.event_contract_fingerprint
    _program_resident_state_value = ports.resident_state_value
    _read_lineage = ports.read_lineage
    _read_resident_activity_snapshot = ports.read_resident_activity_snapshot
    _role_file_signature = ports.role_file_signature
    _scheduled_time_reached = ports.scheduled_time_reached
    _lineage_scope = ports.lineage_scope
    dispatch_lineage_program_event = ports.dispatch_program_event
    actor_id = _safe_actor(actor)
    with _lineage_scope(lineage_id):
        preview = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(preview, actor_id)
        preview_plan = _compiled_program(preview)
        preview_nodes = {
            str(node.get("id") or ""): node
            for node in preview.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "")
        }
        activity_sources = _resident_activity_event_sources(preview_plan)
        pressure_sources = _resident_context_pressure_event_sources(
            preview_plan
        )
        state_condition_sources = _graph_state_condition_event_sources(
            preview_plan
        )
        message_sources = lineage_message_events.message_received_sources(
            preview_plan
        )
        requested_ids: list[str] | None = []
        if any(
            str(source.get("event_type") or "")
            == "residents.all_idle"
            for source in activity_sources
        ):
            requested_ids = None
        elif activity_sources:
            requested_ids = sorted({
                *preview_nodes,
                *(
                    str(value)
                    for source in activity_sources
                    for value in [
                        *(source.get("resident_ids") or []),
                        *lineage_program.event_source_subject_ids(source),
                    ]
                    if str(value)
                ),
            })
        pressure_session_keys = {
            _lineage_node_session_key(subject)
            for source in pressure_sources
            for subject in [
                preview_nodes.get(subject_id)
                for subject_id in lineage_program.event_source_subject_ids(source)
            ]
            if isinstance(subject, dict)
            and _lineage_node_session_key(subject)
        }
    if resident_activity_snapshot is None and activity_sources:
        # Resident registry and job-file scans do not belong to the Lineage
        # graph lock. Capture them before entering Event reconciliation.
        resident_activity_snapshot = _read_resident_activity_snapshot(
            requested_ids
        )
    if resident_activity_snapshot is not None and activity_sources:
        resident_activity_snapshot = _with_facade_activity(resident_activity_snapshot, activity_sources)
    # Conversation logs belong to the Resident subsystem; read them outside
    # the Lineage graph lock like the other Resident facts.
    message_prefetch = lineage_message_events.prefetch_message_arrivals(
        lineage_id, message_sources, actor_id,
    )
    release_prefetch = lineage_release_events.prefetch(lineage_id, preview_plan)
    graph_state_snapshot: dict[str, Any] | None = None
    if state_condition_sources:
        # Graph State owns its own lock and persistence contract. Read its
        # narrow live projection outside the Lineage graph lock.
        graph_state_snapshot = lineage_graph_state.read_state(
            lineage_id
        )
    resident_context_pressures: dict[str, dict[str, Any]] = {}
    if pressure_session_keys:
        # Context usage is owned by the Resident execution subsystem. The
        # Event runtime consumes only its actor-scoped percentage projection.

        resident_context_pressures = (
            resident_domain.get_resident_context_pressures(
                sorted(pressure_session_keys),
                actor_id,
            )
        )

    pending: list[dict[str, Any]] = []
    baseline_count = 0
    wake_times: list[datetime] = []
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor_id)
        plan = _compiled_program(lineage)
        nodes = {
            str(node.get("id") or ""): node
            for node in lineage.get("nodes") or []
            if isinstance(node, dict)
        }
        runtime = lineage_program_runtime.read_runtime(
            lineage_id
        )
        runtime_sources = (
            runtime.get("event_sources")
            if isinstance(runtime.get("event_sources"), dict)
            else {}
        )
        resident_state_cache: dict[str, str] = {}
        recent_invocations: list[dict[str, Any]] | None = None
        scoped_from, scoped_subject = 0, ""
        for event_source_id, event_source, subject_scope in _observed_sources(plan):
            _scope_pending(pending, scoped_from, scoped_subject)
            scoped_from, scoped_subject = len(pending), subject_scope
            if not isinstance(event_source, dict):
                continue
            if (runtime_sources.get(event_source_id) or {}).get("paused"):
                # Paused sources observe nothing; resuming re-establishes baselines.
                continue
            event_type = str(event_source.get("event_type") or "")
            if lineage_program_runtime.limit_reached(
                lineage_program_runtime.normalize_optional_limit(event_source.get("max_fires")),
                max(0, int((runtime_sources.get(event_source_id) or {}).get("fire_count") or 0)),
            ):
                # An exhausted source never fires again; it needs no wake.
                exhausted_source = True
            else:
                exhausted_source = False
            if event_type == lineage_release_events.EVENT and not exhausted_source:
                wake_times.append(datetime.now(timezone.utc) + timedelta(seconds=lineage_release_events.POLL_SECONDS))
                batch = release_prefetch.get(event_source_id)
                if batch is None:
                    continue
                if batch['baseline'] is not None:
                    signature = lineage_release_events.value(batch['baseline'])
                    observed = lineage_program_runtime.observe_event_source_value(
                        lineage_id, event_source_id, locator=lineage_release_events.LOCATOR,
                        signature=signature, value=signature)
                    baseline_count += int(observed['status'] == 'baseline')
                else:
                    state = runtime_sources.get(event_source_id, {})
                    if state.get('state_signature') == batch['previous']:
                        pending.extend(lineage_release_events.pending(
                            event_source_id, event_source, batch, _program_event_source_contract_fingerprint))
                continue
            if event_type == "file.changed" and not exhausted_source:
                # File sources have no due time; they are looked at again soon.
                wake_times.append(datetime.now(timezone.utc) + timedelta(seconds=FILE_SOURCE_POLL_SECONDS))
            if event_type == "residents.all_finished":
                expected = set(event_source.get("resident_ids") or [])
                configured_path_id = str(
                    event_source.get("trigger_path_id") or "$trigger_path.id"
                )
                if recent_invocations is None:
                    # Runs still in progress or settled recently; older Runs
                    # already had their chance to finish.
                    recent_invocations = _recent_resident_inputs(lineage_id)
                trigger_path_ids = sorted({
                    str(item.get("trigger_path_id") or "")
                    for item in recent_invocations
                    if str(item.get("trigger_path_id") or "")
                    and (
                        configured_path_id == "$trigger_path.id"
                        or str(item.get("trigger_path_id") or "")
                        == configured_path_id
                    )
                })
                for trigger_path_id in trigger_path_ids:
                    run_invocations = [
                        item
                        for item in recent_invocations
                        if str(item.get("trigger_path_id") or "") == trigger_path_id
                    ]
                    statuses = {
                        resident_id: [
                            str(item.get("status") or "")
                            for item in run_invocations
                            if str(item.get("resident_id") or "")
                            == resident_id
                        ]
                        for resident_id in expected
                    }
                    if not expected or any(
                        not values
                        or any(value != "completed" for value in values)
                        for values in statuses.values()
                    ):
                        continue
                    event = lineage_primitives.make_event(
                        "residents.all_finished",
                        payload={
                            "trigger_path_id": trigger_path_id,
                            "resident_ids": sorted(expected),
                            "resident_count": len(expected),
                        },
                        correlation=f"{event_source_id}:{trigger_path_id}",
                    )
                    pending.append(
                        {
                            "event_source_id": event_source_id,
                            "event": event,
                            "cursor": f"resident-run-finished:{trigger_path_id}",
                            "source_contract": (
                                _program_event_source_contract_fingerprint(
                                    event_source
                                )
                            ),
                        }
                    )
                continue
            if event_type == lineage_message_events.MESSAGE_RECEIVED_EVENT:
                prefetched = message_prefetch.get((event_source_id, subject_scope))
                if prefetched is None or exhausted_source:
                    continue
                locator = prefetched["locator"]
                if prefetched["base_signature"] is None:
                    # First look: later messages count, earlier ones do not.
                    observed = lineage_program_runtime.observe_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        signature=lineage_message_events.cursor_signature(prefetched["cursor"]),
                        value=lineage_message_events.cursor_value(prefetched["cursor"]),
                        subject=subject_scope,
                    )
                    if observed["status"] == "baseline":
                        baseline_count += 1
                    continue
                stored = lineage_program_runtime.observation_state(runtime_sources, event_source_id, subject_scope)
                if (
                    str(stored.get("state_locator") or "") != locator
                    or str(stored.get("state_signature") or "") != prefetched["base_signature"]
                ):
                    # The baseline moved after the prefetch; the next pass rereads.
                    continue
                previous_signature = prefetched["base_signature"]
                if not prefetched["arrivals"]:
                    if prefetched["cursor"] != prefetched["base_cursor"]:
                        lineage_program_runtime.commit_event_source_value(
                            lineage_id,
                            event_source_id,
                            locator=locator,
                            previous_signature=previous_signature,
                            signature=lineage_message_events.cursor_signature(prefetched["cursor"]),
                            value=lineage_message_events.cursor_value(prefetched["cursor"]),
                            subject=subject_scope,
                        )
                    continue
                subject_id = prefetched["subject_id"]
                for arrival in prefetched["arrivals"]:
                    signature = lineage_message_events.cursor_signature(arrival["cursor"])
                    event = lineage_message_events.arrival_event(event_source_id, subject_id, arrival)
                    # Each arrival advances the cursor only from the previous
                    # one, so an Event that is not accepted is read again.
                    pending.append({
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": lineage_message_events.arrival_cursor(subject_id, arrival.get("occurrence_id") or arrival["message_id"]),
                        "source_contract": _program_event_source_contract_fingerprint(event_source),
                        "state_locator": locator,
                        "previous_state_signature": previous_signature,
                        "state_signature": signature,
                        "state_value": lineage_message_events.cursor_value(arrival["cursor"]),
                    })
                    previous_signature = signature
                continue
            if event_type == "resident.work_started":
                subject_id = str(
                    event_source.get("subject_resident_id") or ""
                )
                activity_state = (
                    (resident_activity_snapshot or {})
                    .get("residents", {})
                    .get(subject_id, {})
                )
                active = bool(activity_state.get("active"))
                message_id = str(activity_state.get("message_id") or "")
                value = f"active:{message_id}" if active and message_id else "idle"
                locator = "resident_work_locator_" + hashlib.sha256(
                    subject_id.encode("utf-8")
                ).hexdigest()
                signature = "sha256:" + hashlib.sha256(
                    value.encode("utf-8")
                ).hexdigest()
                observed = lineage_program_runtime.observe_event_source_value(
                    lineage_id,
                    event_source_id,
                    locator=locator,
                    signature=signature,
                    value=value,
                    subject=subject_scope,
                )
                baseline_active = (
                    observed["status"] == "baseline"
                    and active
                    and bool(message_id)
                    and not observed.get("resumed")
                )
                if observed["status"] == "baseline" and not baseline_active:
                    baseline_count += 1
                    continue
                if observed["status"] not in {"baseline", "changed"}:
                    continue
                if not active or not message_id:
                    lineage_program_runtime.commit_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        previous_signature=observed[
                            "previous_signature"
                        ],
                        signature=signature,
                        value=value,
                        subject=subject_scope,
                    )
                    continue
                occurrence = int(observed.get("generation") or 0) + 1
                event = lineage_primitives.make_event(
                    "resident.work_started",
                    subject_resident_id=subject_id,
                    occurred_at=str(activity_state.get("started_at") or ""),
                    payload={
                        "message_id": message_id,
                        "status": str(activity_state.get("status") or ""),
                        **(
                            {"member_resident_id": str(activity_state["member_resident_id"])}
                            if activity_state.get("member_resident_id")
                            else {}
                        ),
                    },
                    correlation=f"{event_source_id}:{subject_id}:{message_id}",
                )
                pending_item = {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": f"message-started:{subject_id}:{message_id}",
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                    }
                if not baseline_active:
                    pending_item.update(
                        {
                            "state_locator": locator,
                            "previous_state_signature": observed[
                                "previous_signature"
                            ],
                            "state_signature": signature,
                            "state_value": value,
                        }
                    )
                pending.append(pending_item)
                continue
            if event_type == "resident.work_duration_exceeded":
                subject_id = str(
                    event_source.get("subject_resident_id") or ""
                )
                activity_state = (
                    (resident_activity_snapshot or {})
                    .get("residents", {})
                    .get(subject_id, {})
                )
                message_id = str(activity_state.get("message_id") or "")
                started_at = str(activity_state.get("started_at") or "")
                captured_at = str(
                    (resident_activity_snapshot or {}).get("captured_at")
                    or ""
                )
                if not activity_state.get("active") or not message_id:
                    if activity_state and lineage_program_runtime.observation_state(
                        runtime_sources, event_source_id, subject_scope,
                    ).get("rebaseline"):
                        _observe_boolean_event_condition(
                            lineage_id, event_source_id,
                            locator=f"resident_work_duration_idle:{subject_id}",
                            matched=False, fire_on_initial_match=False,
                            subject=subject_scope,
                        )
                    continue
                duration_seconds = _elapsed_whole_seconds(
                    started_at,
                    captured_at,
                )
                if duration_seconds is None:
                    continue
                threshold_seconds = max(
                    1,
                    int(event_source.get("duration_seconds") or 900),
                )
                locator = "resident_work_duration_locator_" + (
                    hashlib.sha256(
                        (
                            f"{subject_id}\0{message_id}\0"
                            f"{threshold_seconds}"
                        ).encode("utf-8")
                    ).hexdigest()
                )
                condition = _observe_boolean_event_condition(
                    lineage_id,
                    event_source_id,
                    locator=locator,
                    matched=duration_seconds > threshold_seconds,
                    fire_on_initial_match=True,
                    subject=subject_scope,
                )
                if (
                    condition["status"] == "baseline"
                    and not condition["should_emit"]
                ):
                    baseline_count += 1
                    continue
                if condition["should_commit"]:
                    _commit_boolean_event_condition(
                        lineage_id,
                        event_source_id,
                        condition,
                    )
                    continue
                if not condition["should_emit"]:
                    continue
                occurrence = condition["observed"]["occurrence_key"]
                event = lineage_primitives.make_event(
                    "resident.work_duration_exceeded",
                    subject_resident_id=subject_id,
                    occurred_at=captured_at,
                    payload={
                        "message_id": message_id,
                        "input_state": str(
                            activity_state.get("status") or ""
                        ),
                        "started_at": started_at,
                        "threshold_seconds": threshold_seconds,
                        "duration_seconds": duration_seconds,
                    },
                    correlation=(
                        f"{event_source_id}:{subject_id}:{message_id}:"
                        f"{threshold_seconds}"
                    ),
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": (
                            f"work-duration:{subject_id}:{message_id}:"
                            f"{threshold_seconds}"
                        ),
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        **_boolean_condition_pending_state(condition),
                    }
                )
                continue
            if event_type == "resident.queue_pressure_exceeded":
                subject_id = str(
                    event_source.get("subject_resident_id") or ""
                )
                activity_state = (
                    (resident_activity_snapshot or {})
                    .get("residents", {})
                    .get(subject_id, {})
                )
                captured_at = str(
                    (resident_activity_snapshot or {}).get("captured_at")
                    or ""
                )
                queue_count = max(
                    0,
                    int(activity_state.get("queue_count") or 0),
                )
                oldest_queued_at = str(
                    activity_state.get("oldest_queued_at") or ""
                )
                oldest_wait_seconds = (
                    _elapsed_whole_seconds(oldest_queued_at, captured_at)
                    if oldest_queued_at
                    else 0
                )
                if oldest_wait_seconds is None:
                    oldest_wait_seconds = 0
                pressure_metric = str(
                    event_source.get("pressure_metric") or "queue_length"
                )
                if pressure_metric == "oldest_wait_seconds":
                    threshold = max(
                        1,
                        int(
                            event_source.get("wait_seconds_threshold")
                            or 300
                        ),
                    )
                    pressure_value = oldest_wait_seconds
                else:
                    pressure_metric = "queue_length"
                    threshold = max(
                        1,
                        int(
                            event_source.get("queue_length_threshold")
                            or 3
                        ),
                    )
                    pressure_value = queue_count
                locator = "resident_queue_pressure_locator_" + (
                    hashlib.sha256(
                        (
                            f"{subject_id}\0{pressure_metric}\0{threshold}"
                        ).encode("utf-8")
                    ).hexdigest()
                )
                condition = _observe_boolean_event_condition(
                    lineage_id,
                    event_source_id,
                    locator=locator,
                    matched=pressure_value > threshold,
                    fire_on_initial_match=False,
                    subject=subject_scope,
                )
                if (
                    condition["status"] == "baseline"
                    and not condition["should_emit"]
                ):
                    baseline_count += 1
                    continue
                if condition["should_commit"]:
                    _commit_boolean_event_condition(
                        lineage_id,
                        event_source_id,
                        condition,
                    )
                    continue
                if not condition["should_emit"]:
                    continue
                occurrence = condition["observed"]["occurrence_key"]
                event = lineage_primitives.make_event(
                    "resident.queue_pressure_exceeded",
                    subject_resident_id=subject_id,
                    occurred_at=captured_at,
                    payload={
                        "message_id": str(activity_state.get("message_id") or ""),
                        "input_state": str(
                            activity_state.get("status") or ""
                        ),
                        "pressure_metric": pressure_metric,
                        "pressure_threshold": threshold,
                        "pressure_value": pressure_value,
                        "queue_count": queue_count,
                        "oldest_queued_at": oldest_queued_at,
                        "oldest_wait_seconds": oldest_wait_seconds,
                    },
                    correlation=(
                        f"{event_source_id}:{occurrence}:{locator}"
                    ),
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": (
                            f"queue-pressure:{occurrence}:{locator}{_resident_suffix(subject_scope)}"
                        ),
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        **_boolean_condition_pending_state(condition),
                    }
                )
                continue
            observed_value = _observed_value(
                lineage_id, event_type, event_source, lineage, graph_state_snapshot,
            )
            if observed_value is not None:
                value, payload, emit_values = observed_value
                locator = f"{event_type}:{json.dumps(payload.get('locator') or '', ensure_ascii=False)}"
                locator = "value_locator_" + hashlib.sha256(locator.encode("utf-8")).hexdigest()
                payload.pop("locator", None)
                signature = "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
                observed = lineage_program_runtime.observe_event_source_value(
                    lineage_id,
                    event_source_id,
                    locator=locator,
                    signature=signature,
                    value=value,
                    subject=subject_scope,
                )
                if observed["status"] == "baseline":
                    baseline_count += 1
                    continue
                if observed["status"] != "changed":
                    continue
                if emit_values is not None and value.split(":", 1)[0] not in emit_values:
                    # The subject moved to a state this source does not report.
                    lineage_program_runtime.commit_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        previous_signature=observed["previous_signature"],
                        signature=signature,
                        value=value,
                        subject=subject_scope,
                    )
                    continue
                previous_value = str(observed.get("previous_value") or "")
                if event_type == "lineage.changed":
                    payload["previous_revision"] = max(0, int(previous_value or 0))
                occurrence = observed["occurrence_key"]
                cursor = f"value:{occurrence}:{signature}"
                event = lineage_primitives.make_event(
                    event_type,
                    payload=payload,
                    correlation=f"{event_source_id}:{occurrence}:{signature}",
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": cursor,
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        "state_locator": locator,
                        "previous_state_signature": observed[
                            "previous_signature"
                        ],
                        "state_signature": signature,
                        "state_value": value,
                    }
                )
                continue
            if event_type == "state.condition_met":
                if graph_state_snapshot is None:
                    continue
                state_key = str(event_source.get("key") or "")
                operator = str(
                    event_source.get("operator") or "equals"
                )
                expected_value = _clone_json(
                    event_source.get("expected_value")
                )
                cells = (
                    graph_state_snapshot.get("cells")
                    if isinstance(graph_state_snapshot.get("cells"), dict)
                    else {}
                )
                existed = state_key in cells
                current_value = cells.get(state_key)
                matched = _state_condition_matches(
                    existed=existed,
                    value=current_value,
                    operator=operator,
                    expected_value=expected_value,
                )
                locator_identity = json.dumps(
                    {
                        "key": state_key,
                        "operator": operator,
                        "expected_value": expected_value,
                        "fire_on_initial_match": bool(
                            event_source.get("fire_on_initial_match")
                        ),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                locator = "graph_state_condition_locator_" + (
                    hashlib.sha256(
                        locator_identity.encode("utf-8")
                    ).hexdigest()
                )
                condition = _observe_boolean_event_condition(
                    lineage_id,
                    event_source_id,
                    locator=locator,
                    matched=matched,
                    fire_on_initial_match=bool(
                        event_source.get("fire_on_initial_match")
                    ),
                    subject=subject_scope,
                )
                if (
                    condition["status"] == "baseline"
                    and not condition["should_emit"]
                ):
                    baseline_count += 1
                    continue
                if condition["should_commit"]:
                    _commit_boolean_event_condition(
                        lineage_id,
                        event_source_id,
                        condition,
                    )
                    continue
                if not condition["should_emit"]:
                    continue
                occurrence = condition["observed"]["occurrence_key"]
                state_revision = max(
                    0,
                    int(graph_state_snapshot.get("revision") or 0),
                )
                event = lineage_primitives.make_event(
                    "state.condition_met",
                    occurred_at=str(
                        graph_state_snapshot.get("updated_at") or ""
                    ),
                    payload={
                        "key": state_key,
                        "operator": operator,
                        "state_revision": state_revision,
                        "previous_matched": condition[
                            "previous_matched"
                        ],
                        "matched": True,
                    },
                    correlation=(
                        f"{event_source_id}:{occurrence}:"
                        f"{state_revision}:{locator}"
                    ),
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": (
                            f"state-condition:{occurrence}:"
                            f"{state_revision}:{locator}"
                        ),
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        **_boolean_condition_pending_state(condition),
                    }
                )
                continue
            if event_type == "residents.all_idle":
                activity = resident_activity_snapshot or {}
                member_ids = _all_resident_activity_member_ids(activity)
                active_ids = {
                    str(value)
                    for value in activity.get("active_resident_ids") or []
                    if str(value)
                }
                running_count = sum(
                    resident_id in active_ids
                    for resident_id in member_ids
                )
                resident_scope = "system"
                locator = "resident_set_locator_" + hashlib.sha256(
                    b"system\0system"
                ).hexdigest()
                value = str(running_count)
                signature = "sha256:" + hashlib.sha256(
                    value.encode("utf-8")
                ).hexdigest()
                observed = (
                    lineage_program_runtime
                    .observe_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        signature=signature,
                        value=value,
                    )
                )
                if observed["status"] == "baseline":
                    baseline_count += 1
                    continue
                if observed["status"] != "changed":
                    continue
                previous_running_count = max(
                    0,
                    int(observed.get("previous_value") or 0),
                )
                if not (
                    previous_running_count > 0
                    and running_count == 0
                ):
                    lineage_program_runtime.commit_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        previous_signature=observed[
                            "previous_signature"
                        ],
                        signature=signature,
                        value=value,
                        subject=subject_scope,
                    )
                    continue
                occurrence = int(observed.get("generation") or 0) + 1
                cursor = (
                    f"resident-set-idle:{occurrence}:{locator}"
                )
                event = lineage_primitives.make_event(
                    "residents.all_idle",
                    payload={
                        "resident_scope": resident_scope,
                        "resident_count": len(member_ids),
                        "previous_running_count": (
                            previous_running_count
                        ),
                        "running_count": 0,
                    },
                    correlation=(
                        f"{event_source_id}:{occurrence}:{locator}"
                    ),
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": cursor,
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        "state_locator": locator,
                        "previous_state_signature": observed[
                            "previous_signature"
                        ],
                        "state_signature": signature,
                        "state_value": value,
                    }
                )
                continue
            if event_type == "resident.context_pressure_exceeded":
                subject_id = str(
                    event_source.get("subject_resident_id") or ""
                )
                subject = nodes.get(subject_id)
                if not subject:
                    continue
                pressure = resident_context_pressures.get(
                    _lineage_node_session_key(subject),
                    {},
                )
                raw_used_percent = pressure.get("used_percent")
                try:
                    used_percent = round(float(raw_used_percent), 1)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(used_percent) or used_percent < 0:
                    continue
                raw_threshold_percent = event_source.get(
                    "threshold_percent"
                )
                threshold_percent = round(
                    float(
                        raw_threshold_percent
                        if raw_threshold_percent is not None
                        else 85
                    ),
                    1,
                )
                value = f"{used_percent:.1f}"
                resident_id = _lineage_node_session_key(subject)
                locator = "context_pressure_locator_" + hashlib.sha256(
                    (
                        f"{subject_id}\0{resident_id}"
                        f"\0{threshold_percent:.1f}"
                    ).encode("utf-8")
                ).hexdigest()
                signature = "sha256:" + hashlib.sha256(
                    value.encode("utf-8")
                ).hexdigest()
                observed = (
                    lineage_program_runtime.observe_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        signature=signature,
                        value=value,
                        subject=subject_scope,
                    )
                )
                status = str(observed.get("status") or "")
                exceeded = used_percent > threshold_percent
                previous_used_percent: float | None = None
                if status == "baseline":
                    if not exceeded or observed.get("resumed"):
                        baseline_count += 1
                        continue
                elif status == "changed":
                    try:
                        previous_used_percent = round(
                            float(observed.get("previous_value")),
                            1,
                        )
                    except (TypeError, ValueError):
                        previous_used_percent = None
                    if not (
                        previous_used_percent is not None
                        and previous_used_percent <= threshold_percent
                        and exceeded
                    ):
                        lineage_program_runtime.commit_event_source_value(
                            lineage_id,
                            event_source_id,
                            locator=locator,
                            previous_signature=observed[
                                "previous_signature"
                            ],
                            signature=signature,
                            value=value,
                            subject=subject_scope,
                        )
                        continue
                else:
                    continue
                occurrence = int(observed.get("generation") or 0) + 1
                event = lineage_primitives.make_event(
                    "resident.context_pressure_exceeded",
                    subject_resident_id=subject_id,
                    occurred_at=str(pressure.get("updated_at") or ""),
                    payload={
                        "threshold_percent": threshold_percent,
                        "previous_used_percent": previous_used_percent,
                        "used_percent": used_percent,
                        "context_tokens": max(
                            0,
                            int(pressure.get("context_tokens") or 0),
                        ),
                        "model_context_window": max(
                            0,
                            int(
                                pressure.get("model_context_window") or 0
                            ),
                        ),
                        "heat_level": str(
                            pressure.get("heat_level") or "normal"
                        ),
                    },
                    correlation=(
                        f"{event_source_id}:{occurrence}:{signature}"
                    ),
                )
                pending_item = {
                    "event_source_id": event_source_id,
                    "event": event,
                    "cursor": (
                        f"context-pressure:{occurrence}:{signature}{_resident_suffix(subject_scope)}"
                    ),
                    "source_contract": (
                        _program_event_source_contract_fingerprint(
                            event_source
                        )
                    ),
                }
                if status == "changed":
                    pending_item.update(
                        {
                            "state_locator": locator,
                            "previous_state_signature": observed[
                                "previous_signature"
                            ],
                            "state_signature": signature,
                            "state_value": value,
                        }
                    )
                pending.append(pending_item)
                continue
            if event_type == "resident.state_changed":
                subject_id = str(
                    event_source.get("subject_resident_id") or ""
                )
                state_domain = str(
                    event_source.get("state_domain") or "intake"
                )
                subject = nodes.get(subject_id)
                if not subject:
                    continue
                state_value = _program_resident_state_value(
                    lineage,
                    subject,
                    state_domain,
                    actor_id,
                    resident_state_cache,
                )
                locator = "state_locator_" + hashlib.sha256(
                    f"{subject_id}\0{state_domain}".encode("utf-8")
                ).hexdigest()
                signature = "sha256:" + hashlib.sha256(
                    state_value.encode("utf-8")
                ).hexdigest()
                observed = (
                    lineage_program_runtime.observe_event_source_value(
                        lineage_id,
                        event_source_id,
                        locator=locator,
                        signature=signature,
                        value=state_value,
                        subject=subject_scope,
                    )
                )
                if observed["status"] == "baseline":
                    baseline_count += 1
                    continue
                if observed["status"] != "changed":
                    continue
                occurrence = int(observed.get("generation") or 0) + 1
                cursor = f"state:{occurrence}:{signature}{_resident_suffix(subject_scope)}"
                event = lineage_primitives.make_event(
                    "resident.state_changed",
                    subject_resident_id=subject_id,
                    payload={
                        "state_domain": state_domain,
                        "previous_state": str(
                            observed.get("previous_value") or ""
                        ),
                        "state": state_value,
                    },
                    correlation=(
                        f"{event_source_id}:{occurrence}:{signature}"
                    ),
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": cursor,
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        "state_locator": locator,
                        "previous_state_signature": observed[
                            "previous_signature"
                        ],
                        "state_signature": signature,
                        "state_value": state_value,
                    }
                )
                continue
            if event_type == "time.reached":
                schedule_mode = str(
                    event_source.get("schedule_mode") or "at"
                )
                timer: dict[str, Any] = {}
                recurring: dict[str, Any] = {}
                if schedule_mode == "recurring":
                    recurring = (
                        lineage_program_runtime.observe_recurring_schedule(
                            lineage_id,
                            event_source_id,
                            config=event_source,
                        )
                    )
                    # Wake again when the next occurrence is due.
                    if not exhausted_source:
                        _note_wake(wake_times, recurring.get("next_due_at"))
                    if recurring.get("status") != "due":
                        continue
                    scheduled_at = str(
                        recurring.get("scheduled_at") or ""
                    )
                    cursor = (
                        f"recurring:{int(recurring.get('generation') or 0)}:"
                        f"{scheduled_at}"
                    )
                elif schedule_mode == "countdown":
                    timer = lineage_program_runtime.ensure_countdown_timer(
                        lineage_id,
                        event_source_id,
                        delay_seconds=int(
                            event_source.get("delay_seconds") or 60
                        ),
                    )
                    scheduled_at = str(timer.get("due_at") or "")
                    cursor = (
                        f"timer:{int(timer.get('generation') or 0)}:"
                        f"{scheduled_at}"
                    )
                else:
                    scheduled_at = str(
                        event_source.get("scheduled_at") or ""
                    )
                    cursor = f"time:{scheduled_at}"
                source_runtime = runtime_sources.get(event_source_id) or {}
                recognized_cursors = {
                    *(
                        source_runtime.get("consumed_cursors")
                        or []
                    ),
                    *(source_runtime.get("fired_cursors") or []),
                }
                if (
                    scheduled_at
                    and not exhausted_source
                    and schedule_mode != "recurring"
                    and cursor not in recognized_cursors
                    and not _scheduled_time_reached(scheduled_at)
                ):
                    _note_wake(wake_times, scheduled_at)
                if (
                    not scheduled_at
                    or not _scheduled_time_reached(scheduled_at)
                    or (
                        cursor in recognized_cursors
                        and schedule_mode != "recurring"
                    )
                ):
                    continue
                event = lineage_primitives.make_event(
                    "time.reached",
                    payload={
                        "scheduled_at": scheduled_at,
                        "schedule_mode": schedule_mode,
                        **(
                            {
                                "observed_at": str(
                                    recurring.get("observed_at") or ""
                                ),
                                "missed_count": int(
                                    recurring.get("missed_count") or 0
                                ),
                                "generation": int(
                                    recurring.get("generation") or 0
                                ),
                            }
                            if schedule_mode == "recurring"
                            else {}
                        ),
                        **(
                            {
                                "started_at": str(
                                    timer.get("started_at") or ""
                                ),
                                "delay_seconds": int(
                                    timer.get("delay_seconds") or 0
                                ),
                            }
                            if schedule_mode == "countdown"
                            else {}
                        ),
                    },
                    correlation=f"{event_source_id}:{scheduled_at}",
                )
                pending.append(
                    {
                        "event_source_id": event_source_id,
                        "event": event,
                        "cursor": cursor,
                        "source_contract": (
                            _program_event_source_contract_fingerprint(
                                event_source
                            )
                        ),
                        **(
                            {
                                "recurring_generation": int(
                                    recurring.get("generation") or 0
                                ),
                                "recurring_scheduled_at": scheduled_at,
                            }
                            if schedule_mode == "recurring"
                            else {}
                        ),
                    }
                )
                continue
            if event_type != "file.changed":
                continue
            subject_id = str(
                event_source.get("subject_resident_id") or ""
            )
            relative_path = str(event_source.get("path") or "")
            subject = nodes.get(subject_id)
            if not subject or not relative_path:
                continue
            signature = _role_file_signature(
                lineage_id,
                subject,
                relative_path,
            )
            locator = "file_locator_" + hashlib.sha256(
                f"{subject_id}\0{relative_path}".encode("utf-8")
            ).hexdigest()
            observed = lineage_program_runtime.observe_file_signature(
                lineage_id,
                event_source_id,
                locator=locator,
                signature=signature,
            )
            if observed["status"] == "baseline":
                baseline_count += 1
                continue
            if observed["status"] != "changed":
                continue
            occurrence = int(observed.get("generation") or 0) + 1
            cursor = f"file:{occurrence}:{signature}"
            if cursor in (
                (runtime_sources.get(event_source_id) or {}).get(
                    "consumed_cursors"
                )
                or []
            ):
                lineage_program_runtime.commit_file_signature(
                    lineage_id,
                    event_source_id,
                    locator=locator,
                    previous_signature=observed["previous_signature"],
                    signature=signature,
                )
                continue
            event = lineage_primitives.make_event(
                "file.changed",
                subject_resident_id=subject_id,
                payload={
                    "path": relative_path,
                    "file_signature": signature,
                },
                correlation=f"{event_source_id}:{occurrence}:{signature}",
            )
            pending.append(
                {
                    "event_source_id": event_source_id,
                    "event": event,
                    "cursor": cursor,
                    "source_contract": (
                        _program_event_source_contract_fingerprint(
                            event_source
                        )
                    ),
                    "file_locator": locator,
                    "previous_signature": observed[
                        "previous_signature"
                    ],
                    "file_signature": signature,
                }
            )

    _scope_pending(pending, scoped_from, scoped_subject)
    events: list[dict[str, Any]] = []
    event_count = 0
    for item in pending:
        result = _dispatch_observation(lineage_id, item, actor_id, dispatch_lineage_program_event)
        events.append(result)
        accepted = bool(
            int(result.get("matched_count") or 0)
            or int(result.get("consumed_count") or 0)
            or result.get("duplicate")
            or bool((result.get("fire") or {}).get("accepted"))
        )
        if int(result.get("matched_count") or 0):
            event_count += 1
        if (
            item.get("recurring_scheduled_at")
            and (accepted or result.get("exhausted"))
        ):
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
                previous_signature=item[
                    "previous_state_signature"
                ],
                signature=item["state_signature"],
                value=item["state_value"],
                subject=str(item.get("state_subject") or ""),
            )
    return {
        "baseline_count": baseline_count,
        "event_count": event_count,
        "events": events,
        # When a time or file source of this Lineage needs to be looked at
        # again; the Lineage coordinator wakes for it.
        "next_wake_at": _iso_wake(min(wake_times)) if wake_times else "",
    }


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

    The per-Resident view names that Resident as its subject, so every branch
    below observes one Resident; its contract fingerprint stays the source's.
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


def _resident_suffix(subject: str) -> str:
    """Occurrence keys of a source watching several Residents name the Resident."""

    return f":{subject}" if subject else ""


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

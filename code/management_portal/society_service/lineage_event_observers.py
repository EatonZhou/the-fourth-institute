"""What each system Event type observes, and when it fires.

A reconciliation pass (``lineage_event_reconciliation``) gathers the facts it
needs outside the Lineage graph lock, then walks the Event Sources under the
lock and calls the observer ``OBSERVERS`` registers for each source's
``event_type``. An observer compares the live fact with the baseline the
runtime recognized for that source and then does one of four things:

* records a first baseline (``scan.baseline_count``);
* commits a change that fires nothing, such as work ending;
* lists a candidate Event (``scan.emit``) together with the baseline commit
  that waits until the Event is accepted;
* notes when the source must be looked at again (``scan.wake``).

Locators, signatures, cursors and correlations are persisted or compared with
persisted values, so their formats are part of the runtime contract.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, NamedTuple

from . import lineage_message_events, lineage_primitives, lineage_program_runtime, lineage_release_events, trigger_paths
from .lineage_event_conditions import (
    _boolean_condition_pending_state, _commit_boolean_event_condition,
    _elapsed_whole_seconds, _observe_boolean_event_condition, _state_condition_matches,
)


# A file source has no due time, so its Lineage is looked at again this often.
FILE_SOURCE_POLL_SECONDS = 60
# A Run whose Residents all finished longer ago than this already had its
# chance to fire residents.all_finished.  Looking at every historical Run fires
# again those whose fire cursor predates bounded trigger paths or has aged out
# of the source's last MAX_EVENT_CURSORS cursors.
RESIDENTS_FINISHED_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Facts:
    """What one pass read outside the Lineage graph lock, owned by other subsystems."""

    resident_activity: dict[str, Any] | None = None
    message_arrivals: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    release_publications: dict[str, dict[str, Any]] = field(default_factory=dict)
    graph_state: dict[str, Any] | None = None
    context_pressures: dict[str, dict[str, Any]] = field(default_factory=dict)


class Source(NamedTuple):
    """One Event Source as observed: once, or once per Resident it watches.

    ``config`` is the compiled source; for one of several watched Residents it
    names that Resident as ``subject_resident_id`` and ``subject`` is its ID.
    """

    id: str
    config: dict[str, Any]
    subject: str
    exhausted: bool

    @property
    def event_type(self) -> str:
        return str(self.config.get("event_type") or "")

    @property
    def subject_id(self) -> str:
        return str(self.config.get("subject_resident_id") or "")


@dataclass
class Scan:
    """One observation pass over a Lineage's Event Sources, under its graph lock."""

    lineage_id: str
    actor_id: str
    lineage: dict[str, Any]
    nodes: dict[str, dict[str, Any]]
    runtime_sources: dict[str, Any]
    facts: Facts
    ports: Any
    pending: list[dict[str, Any]] = field(default_factory=list)
    baseline_count: int = 0
    wake_times: list[datetime] = field(default_factory=list)
    resident_states: dict[str, str] = field(default_factory=dict)
    _recent_inputs: list[dict[str, Any]] | None = None

    def runtime_state(self, source: Source) -> dict[str, Any]:
        return self.runtime_sources.get(source.id) or {}

    def activity(self, resident_id: str) -> dict[str, Any]:
        return ((self.facts.resident_activity or {}).get("residents") or {}).get(resident_id) or {}

    def captured_at(self) -> str:
        return str((self.facts.resident_activity or {}).get("captured_at") or "")

    def recent_inputs(self) -> list[dict[str, Any]]:
        """Resident leaves of Runs in progress or settled recently, read once per pass."""
        if self._recent_inputs is None:
            self._recent_inputs = _recent_resident_inputs(self.lineage_id)
        return self._recent_inputs

    def wake(self, moment: datetime) -> None:
        self.wake_times.append(moment)

    def wake_at(self, value: object) -> None:
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
            self.wake_times.append(moment)

    def observe(self, source: Source, locator: str, signature: str, value: str) -> dict[str, Any]:
        """Compare one scalar with the source's recognized baseline."""
        return lineage_program_runtime.observe_event_source_value(
            self.lineage_id, source.id, locator=locator, signature=signature, value=value,
            subject=source.subject,
        )

    def commit(self, source: Source, locator: str, previous_signature: str, signature: str, value: str) -> None:
        """Move the source's baseline to a value that fires nothing."""
        lineage_program_runtime.commit_event_source_value(
            self.lineage_id, source.id, locator=locator, previous_signature=previous_signature,
            signature=signature, value=value, subject=source.subject,
        )

    def edge(self, source: Source, *, locator: str, matched: bool,
             fire_on_initial_match: bool) -> dict[str, Any] | None:
        """The predicate's observation when its edge fires an Event.

        Without an Event it records the first baseline or commits the change
        and returns ``None``.
        """
        condition = _observe_boolean_event_condition(
            self.lineage_id, source.id, locator=locator, matched=matched,
            fire_on_initial_match=fire_on_initial_match, subject=source.subject,
        )
        if condition["status"] == "baseline" and not condition["should_emit"]:
            self.baseline_count += 1
            return None
        if condition["should_commit"]:
            _commit_boolean_event_condition(self.lineage_id, source.id, condition)
            return None
        return condition if condition["should_emit"] else None

    def emit(self, source: Source, event: lineage_primitives.LineageEvent, cursor: str, **commit: Any) -> None:
        """List a candidate Event; ``commit`` is the baseline move made once it is accepted."""
        self.pending.append({
            "event_source_id": source.id,
            "event": event,
            "cursor": cursor,
            "source_contract": self.ports.event_contract_fingerprint(source.config),
            **commit,
        })


def _locator(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _signature(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _value_commit(locator: str, previous_signature: str, signature: str, value: str) -> dict[str, str]:
    """The baseline move of a value source, made after its Event is accepted."""
    return {
        "state_locator": locator,
        "previous_state_signature": previous_signature,
        "state_signature": signature,
        "state_value": value,
    }


def _resident_suffix(subject: str) -> str:
    """Occurrence keys of a source watching several Residents name the Resident."""
    return f":{subject}" if subject else ""


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


def _release_published(scan: Scan, source: Source) -> None:
    """A Portal release was published since the source's last recognized publication."""
    if source.exhausted:
        return
    scan.wake(datetime.now(timezone.utc) + timedelta(seconds=lineage_release_events.POLL_SECONDS))
    batch = scan.facts.release_publications.get(source.id)
    if batch is None:
        return
    if batch["baseline"] is not None:
        signature = lineage_release_events.value(batch["baseline"])
        observed = scan.observe(source, lineage_release_events.LOCATOR, signature, signature)
        scan.baseline_count += int(observed["status"] == "baseline")
    elif scan.runtime_state(source).get("state_signature") == batch["previous"]:
        scan.pending.extend(lineage_release_events.pending(
            source.id, source.config, batch, scan.ports.event_contract_fingerprint))


def _file_changed(scan: Scan, source: Source) -> None:
    """A file of a Resident's Workspace has new content; only its signature is kept."""
    if not source.exhausted:
        # File sources have no due time; they are looked at again soon.
        scan.wake(datetime.now(timezone.utc) + timedelta(seconds=FILE_SOURCE_POLL_SECONDS))
    subject_id = source.subject_id
    relative_path = str(source.config.get("path") or "")
    subject = scan.nodes.get(subject_id)
    if not subject or not relative_path:
        return
    signature = scan.ports.role_file_signature(scan.lineage_id, subject, relative_path)
    locator = _locator("file_locator_", subject_id, relative_path)
    observed = lineage_program_runtime.observe_file_signature(
        scan.lineage_id, source.id, locator=locator, signature=signature)
    if observed["status"] == "baseline":
        scan.baseline_count += 1
        return
    if observed["status"] != "changed":
        return
    occurrence = int(observed.get("generation") or 0) + 1
    cursor = f"file:{occurrence}:{signature}"
    if cursor in (scan.runtime_state(source).get("consumed_cursors") or []):
        lineage_program_runtime.commit_file_signature(
            scan.lineage_id, source.id, locator=locator,
            previous_signature=observed["previous_signature"], signature=signature)
        return
    event = lineage_primitives.make_event(
        "file.changed",
        subject_resident_id=subject_id,
        payload={"path": relative_path, "file_signature": signature},
        correlation=f"{source.id}:{occurrence}:{signature}",
    )
    scan.emit(source, event, cursor, file_locator=locator,
              previous_signature=observed["previous_signature"], file_signature=signature)


def _residents_all_finished(scan: Scan, source: Source) -> None:
    """Every Resident the source names completed its work in one Run."""
    expected = set(source.config.get("resident_ids") or [])
    if not expected:
        return
    configured_path_id = str(source.config.get("trigger_path_id") or "$trigger_path.id")
    runs: dict[str, list[dict[str, Any]]] = {}
    for item in scan.recent_inputs():
        trigger_path_id = str(item.get("trigger_path_id") or "")
        if trigger_path_id and configured_path_id in ("$trigger_path.id", trigger_path_id):
            runs.setdefault(trigger_path_id, []).append(item)
    for trigger_path_id, run in sorted(runs.items()):
        statuses = {
            resident_id: [str(item.get("status") or "") for item in run
                          if str(item.get("resident_id") or "") == resident_id]
            for resident_id in expected
        }
        if any(not values or any(value != "completed" for value in values) for values in statuses.values()):
            continue
        event = lineage_primitives.make_event(
            "residents.all_finished",
            payload={
                "trigger_path_id": trigger_path_id,
                "resident_ids": sorted(expected),
                "resident_count": len(expected),
            },
            correlation=f"{source.id}:{trigger_path_id}",
        )
        scan.emit(source, event, f"resident-run-finished:{trigger_path_id}")


def _message_received(scan: Scan, source: Source) -> None:
    """Messages that reached the watched Resident after the source's cursor."""
    prefetched = scan.facts.message_arrivals.get((source.id, source.subject))
    if prefetched is None or source.exhausted:
        return
    locator = prefetched["locator"]
    if prefetched["base_signature"] is None:
        # First look: later messages count, earlier ones do not.
        observed = scan.observe(
            source, locator,
            lineage_message_events.cursor_signature(prefetched["cursor"]),
            lineage_message_events.cursor_value(prefetched["cursor"]),
        )
        if observed["status"] == "baseline":
            scan.baseline_count += 1
        return
    stored = lineage_program_runtime.observation_state(scan.runtime_sources, source.id, source.subject)
    if (
        str(stored.get("state_locator") or "") != locator
        or str(stored.get("state_signature") or "") != prefetched["base_signature"]
    ):
        # The baseline moved after the prefetch; the next pass rereads.
        return
    previous_signature = prefetched["base_signature"]
    if not prefetched["arrivals"]:
        if prefetched["cursor"] != prefetched["base_cursor"]:
            scan.commit(
                source, locator, previous_signature,
                lineage_message_events.cursor_signature(prefetched["cursor"]),
                lineage_message_events.cursor_value(prefetched["cursor"]),
            )
        return
    subject_id = prefetched["subject_id"]
    for arrival in prefetched["arrivals"]:
        signature = lineage_message_events.cursor_signature(arrival["cursor"])
        event = lineage_message_events.arrival_event(source.id, subject_id, arrival)
        # Each arrival advances the cursor only from the previous one, so an
        # Event that is not accepted is read again.
        scan.emit(
            source, event,
            lineage_message_events.arrival_cursor(subject_id, arrival.get("occurrence_id") or arrival["message_id"]),
            **_value_commit(locator, previous_signature, signature,
                            lineage_message_events.cursor_value(arrival["cursor"])),
        )
        previous_signature = signature


def _work_started(scan: Scan, source: Source) -> None:
    """The watched Resident started working on a message."""
    subject_id = source.subject_id
    activity = scan.activity(subject_id)
    active = bool(activity.get("active"))
    message_id = str(activity.get("message_id") or "")
    value = f"active:{message_id}" if active and message_id else "idle"
    locator = _locator("resident_work_locator_", subject_id)
    signature = _signature(value)
    observed = scan.observe(source, locator, signature, value)
    # Work already running at the first look fires, unless the source only resumed.
    baseline_active = (
        observed["status"] == "baseline" and active and bool(message_id) and not observed.get("resumed")
    )
    if observed["status"] == "baseline" and not baseline_active:
        scan.baseline_count += 1
        return
    if observed["status"] not in {"baseline", "changed"}:
        return
    if not active or not message_id:
        scan.commit(source, locator, observed["previous_signature"], signature, value)
        return
    event = lineage_primitives.make_event(
        "resident.work_started",
        subject_resident_id=subject_id,
        occurred_at=str(activity.get("started_at") or ""),
        payload={
            "message_id": message_id,
            "status": str(activity.get("status") or ""),
            **(
                {"member_resident_id": str(activity["member_resident_id"])}
                if activity.get("member_resident_id")
                else {}
            ),
        },
        correlation=f"{source.id}:{subject_id}:{message_id}",
    )
    commit = {} if baseline_active else _value_commit(locator, observed["previous_signature"], signature, value)
    scan.emit(source, event, f"message-started:{subject_id}:{message_id}", **commit)


def _work_duration_exceeded(scan: Scan, source: Source) -> None:
    """The watched Resident's current work has run longer than the threshold."""
    subject_id = source.subject_id
    activity = scan.activity(subject_id)
    message_id = str(activity.get("message_id") or "")
    started_at = str(activity.get("started_at") or "")
    captured_at = scan.captured_at()
    if not activity.get("active") or not message_id:
        if activity and lineage_program_runtime.observation_state(
            scan.runtime_sources, source.id, source.subject,
        ).get("rebaseline"):
            _observe_boolean_event_condition(
                scan.lineage_id, source.id,
                locator=f"resident_work_duration_idle:{subject_id}",
                matched=False, fire_on_initial_match=False, subject=source.subject,
            )
        return
    duration_seconds = _elapsed_whole_seconds(started_at, captured_at)
    if duration_seconds is None:
        return
    threshold_seconds = max(1, int(source.config.get("duration_seconds") or 900))
    locator = _locator("resident_work_duration_locator_", subject_id, message_id, str(threshold_seconds))
    condition = scan.edge(source, locator=locator, matched=duration_seconds > threshold_seconds,
                          fire_on_initial_match=True)
    if condition is None:
        return
    event = lineage_primitives.make_event(
        "resident.work_duration_exceeded",
        subject_resident_id=subject_id,
        occurred_at=captured_at,
        payload={
            "message_id": message_id,
            "input_state": str(activity.get("status") or ""),
            "started_at": started_at,
            "threshold_seconds": threshold_seconds,
            "duration_seconds": duration_seconds,
        },
        correlation=f"{source.id}:{subject_id}:{message_id}:{threshold_seconds}",
    )
    scan.emit(source, event, f"work-duration:{subject_id}:{message_id}:{threshold_seconds}",
              **_boolean_condition_pending_state(condition))


def _queue_pressure_exceeded(scan: Scan, source: Source) -> None:
    """The watched Resident's queue is longer, or its oldest input older, than the threshold."""
    subject_id = source.subject_id
    activity = scan.activity(subject_id)
    captured_at = scan.captured_at()
    queue_count = max(0, int(activity.get("queue_count") or 0))
    oldest_queued_at = str(activity.get("oldest_queued_at") or "")
    oldest_wait_seconds = (_elapsed_whole_seconds(oldest_queued_at, captured_at) if oldest_queued_at else 0) or 0
    pressure_metric = str(source.config.get("pressure_metric") or "queue_length")
    if pressure_metric == "oldest_wait_seconds":
        threshold = max(1, int(source.config.get("wait_seconds_threshold") or 300))
        pressure_value = oldest_wait_seconds
    else:
        pressure_metric = "queue_length"
        threshold = max(1, int(source.config.get("queue_length_threshold") or 3))
        pressure_value = queue_count
    locator = _locator("resident_queue_pressure_locator_", subject_id, pressure_metric, str(threshold))
    condition = scan.edge(source, locator=locator, matched=pressure_value > threshold,
                          fire_on_initial_match=False)
    if condition is None:
        return
    occurrence = condition["observed"]["occurrence_key"]
    event = lineage_primitives.make_event(
        "resident.queue_pressure_exceeded",
        subject_resident_id=subject_id,
        occurred_at=captured_at,
        payload={
            "message_id": str(activity.get("message_id") or ""),
            "input_state": str(activity.get("status") or ""),
            "pressure_metric": pressure_metric,
            "pressure_threshold": threshold,
            "pressure_value": pressure_value,
            "queue_count": queue_count,
            "oldest_queued_at": oldest_queued_at,
            "oldest_wait_seconds": oldest_wait_seconds,
        },
        correlation=f"{source.id}:{occurrence}:{locator}",
    )
    scan.emit(source, event, f"queue-pressure:{occurrence}:{locator}{_resident_suffix(source.subject)}",
              **_boolean_condition_pending_state(condition))


class ObservedValue(NamedTuple):
    """The current value of a value source and the Event payload that reports it.

    ``locator`` names the observed object; ``reported`` lists the value kinds
    (the part before ``:``) that fire, all of them when ``None``;
    ``previous_field`` names the payload field that carries the previous value.
    """

    value: str
    payload: dict[str, Any]
    locator: str
    reported: frozenset[str] | None = None
    previous_field: str = ""


HANDOFF_EVENT_STATUSES = {
    "workspace_handoff.ready": frozenset({"ready"}),
    "workspace_handoff.failed": frozenset({"failed"}),
}


def _lineage_revision(scan: Scan, source: Source) -> ObservedValue:
    """``lineage.changed`` observes the Lineage version."""
    revision = int(scan.lineage.get("revision") or 0)
    return ObservedValue(str(revision), {"revision": revision}, scan.lineage_id, previous_field="previous_revision")


def _state_cell(scan: Scan, source: Source) -> ObservedValue | None:
    """``state.changed`` observes one shared state key, by a digest of its value."""
    snapshot = scan.facts.graph_state
    if snapshot is None:
        return None
    key = str(source.config.get("key") or "")
    cells = snapshot.get("cells") if isinstance(snapshot.get("cells"), dict) else {}
    if key in cells:
        encoded = json.dumps(cells[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        value = "present:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    else:
        value = "absent"
    return ObservedValue(value, {"key": key, "state_revision": max(0, int(snapshot.get("revision") or 0))}, key)


def _handoff_status(scan: Scan, source: Source) -> ObservedValue | None:
    """``workspace_handoff.*`` observes the status of one handoff and its attempts."""
    handoff_id = str(source.config.get("subject_handoff_id") or "")
    handoff = next(
        (
            edge for edge in scan.lineage.get("edges") or []
            if isinstance(edge, dict) and edge.get("type") == "handoff" and str(edge.get("id") or "") == handoff_id
        ),
        None,
    )
    if not handoff_id or handoff is None:
        return None
    attempts = max([len(item.get("attempts") or []) for item in handoff.get("items") or [] if isinstance(item, dict)] or [0])
    status = str(handoff.get("status") or "draft")
    payload = {"handoff_id": handoff_id, "status": status}
    if status == "failed" and handoff.get("error"):
        payload["diagnostic"] = str(handoff.get("error") or "")[:1000]
    return ObservedValue(f"{status}:{attempts}", payload, handoff_id, HANDOFF_EVENT_STATUSES[source.event_type])


VALUE_READERS: dict[str, Callable[[Scan, Source], ObservedValue | None]] = {
    "lineage.changed": _lineage_revision,
    "state.changed": _state_cell,
    **{event_type: _handoff_status for event_type in HANDOFF_EVENT_STATUSES},
}


def _value_changed(scan: Scan, source: Source) -> None:
    """A value source fires when its value changes to a kind it reports.

    The runtime keeps only a locator, a signature and the short value.
    """
    observed_value = VALUE_READERS[source.event_type](scan, source)
    if observed_value is None:
        return
    value, payload = observed_value.value, dict(observed_value.payload)
    locator = _locator("value_locator_", f"{source.event_type}:{json.dumps(observed_value.locator, ensure_ascii=False)}")
    signature = _signature(value)
    observed = scan.observe(source, locator, signature, value)
    if observed["status"] == "baseline":
        scan.baseline_count += 1
        return
    if observed["status"] != "changed":
        return
    if observed_value.reported is not None and value.split(":", 1)[0] not in observed_value.reported:
        # The subject moved to a state this source does not report.
        scan.commit(source, locator, observed["previous_signature"], signature, value)
        return
    if observed_value.previous_field:
        payload[observed_value.previous_field] = max(0, int(str(observed.get("previous_value") or "") or 0))
    occurrence = observed["occurrence_key"]
    event = lineage_primitives.make_event(
        source.event_type, payload=payload, correlation=f"{source.id}:{occurrence}:{signature}",
    )
    scan.emit(source, event, f"value:{occurrence}:{signature}",
              **_value_commit(locator, observed["previous_signature"], signature, value))


def _state_condition_met(scan: Scan, source: Source) -> None:
    """A shared state key started to satisfy the source's condition."""
    snapshot = scan.facts.graph_state
    if snapshot is None:
        return
    state_key = str(source.config.get("key") or "")
    operator = str(source.config.get("operator") or "equals")
    expected_value = scan.ports.clone_json(source.config.get("expected_value"))
    fire_on_initial_match = bool(source.config.get("fire_on_initial_match"))
    cells = snapshot.get("cells") if isinstance(snapshot.get("cells"), dict) else {}
    matched = _state_condition_matches(
        existed=state_key in cells, value=cells.get(state_key),
        operator=operator, expected_value=expected_value,
    )
    identity = json.dumps(
        {"key": state_key, "operator": operator, "expected_value": expected_value,
         "fire_on_initial_match": fire_on_initial_match},
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    )
    locator = _locator("graph_state_condition_locator_", identity)
    condition = scan.edge(source, locator=locator, matched=matched, fire_on_initial_match=fire_on_initial_match)
    if condition is None:
        return
    occurrence = condition["observed"]["occurrence_key"]
    state_revision = max(0, int(snapshot.get("revision") or 0))
    event = lineage_primitives.make_event(
        "state.condition_met",
        occurred_at=str(snapshot.get("updated_at") or ""),
        payload={
            "key": state_key,
            "operator": operator,
            "state_revision": state_revision,
            "previous_matched": condition["previous_matched"],
            "matched": True,
        },
        correlation=f"{source.id}:{occurrence}:{state_revision}:{locator}",
    )
    scan.emit(source, event, f"state-condition:{occurrence}:{state_revision}:{locator}",
              **_boolean_condition_pending_state(condition))


def _residents_all_idle(scan: Scan, source: Source) -> None:
    """Every Resident of Portal stopped working after at least one was running."""
    activity = scan.facts.resident_activity or {}
    member_ids = sorted({str(value) for value in activity.get("resident_ids") or [] if str(value)})
    active_ids = {str(value) for value in activity.get("active_resident_ids") or [] if str(value)}
    running_count = sum(resident_id in active_ids for resident_id in member_ids)
    locator = _locator("resident_set_locator_", "system", "system")
    value = str(running_count)
    signature = _signature(value)
    observed = scan.observe(source, locator, signature, value)
    if observed["status"] == "baseline":
        scan.baseline_count += 1
        return
    if observed["status"] != "changed":
        return
    previous_running_count = max(0, int(observed.get("previous_value") or 0))
    if not (previous_running_count > 0 and running_count == 0):
        scan.commit(source, locator, observed["previous_signature"], signature, value)
        return
    occurrence = int(observed.get("generation") or 0) + 1
    event = lineage_primitives.make_event(
        "residents.all_idle",
        payload={
            "resident_scope": "system",
            "resident_count": len(member_ids),
            "previous_running_count": previous_running_count,
            "running_count": 0,
        },
        correlation=f"{source.id}:{occurrence}:{locator}",
    )
    scan.emit(source, event, f"resident-set-idle:{occurrence}:{locator}",
              **_value_commit(locator, observed["previous_signature"], signature, value))


def _context_pressure_exceeded(scan: Scan, source: Source) -> None:
    """The watched Resident's context use rose above the threshold percentage."""
    subject_id = source.subject_id
    subject = scan.nodes.get(subject_id)
    if not subject:
        return
    resident_id = scan.ports.lineage_node_session_key(subject)
    pressure = scan.facts.context_pressures.get(resident_id, {})
    try:
        used_percent = round(float(pressure.get("used_percent")), 1)
    except (TypeError, ValueError):
        return
    if not math.isfinite(used_percent) or used_percent < 0:
        return
    raw_threshold_percent = source.config.get("threshold_percent")
    threshold_percent = round(float(raw_threshold_percent if raw_threshold_percent is not None else 85), 1)
    value = f"{used_percent:.1f}"
    locator = _locator("context_pressure_locator_", subject_id, resident_id, f"{threshold_percent:.1f}")
    signature = _signature(value)
    observed = scan.observe(source, locator, signature, value)
    status = str(observed.get("status") or "")
    exceeded = used_percent > threshold_percent
    previous_used_percent: float | None = None
    if status == "baseline":
        # Pressure already above the threshold at the first look fires, unless the source only resumed.
        if not exceeded or observed.get("resumed"):
            scan.baseline_count += 1
            return
    elif status == "changed":
        try:
            previous_used_percent = round(float(observed.get("previous_value")), 1)
        except (TypeError, ValueError):
            previous_used_percent = None
        if not (previous_used_percent is not None and previous_used_percent <= threshold_percent and exceeded):
            scan.commit(source, locator, observed["previous_signature"], signature, value)
            return
    else:
        return
    occurrence = int(observed.get("generation") or 0) + 1
    event = lineage_primitives.make_event(
        "resident.context_pressure_exceeded",
        subject_resident_id=subject_id,
        occurred_at=str(pressure.get("updated_at") or ""),
        payload={
            "threshold_percent": threshold_percent,
            "previous_used_percent": previous_used_percent,
            "used_percent": used_percent,
            "context_tokens": max(0, int(pressure.get("context_tokens") or 0)),
            "model_context_window": max(0, int(pressure.get("model_context_window") or 0)),
            "heat_level": str(pressure.get("heat_level") or "normal"),
        },
        correlation=f"{source.id}:{occurrence}:{signature}",
    )
    commit = _value_commit(locator, observed["previous_signature"], signature, value) if status == "changed" else {}
    scan.emit(source, event, f"context-pressure:{occurrence}:{signature}{_resident_suffix(source.subject)}", **commit)


def _resident_state_changed(scan: Scan, source: Source) -> None:
    """The watched Resident's state in one state domain changed."""
    subject_id = source.subject_id
    state_domain = str(source.config.get("state_domain") or "intake")
    subject = scan.nodes.get(subject_id)
    if not subject:
        return
    state_value = scan.ports.resident_state_value(scan.lineage, subject, state_domain, scan.actor_id,
                                                  scan.resident_states)
    locator = _locator("state_locator_", subject_id, state_domain)
    signature = _signature(state_value)
    observed = scan.observe(source, locator, signature, state_value)
    if observed["status"] == "baseline":
        scan.baseline_count += 1
        return
    if observed["status"] != "changed":
        return
    occurrence = int(observed.get("generation") or 0) + 1
    event = lineage_primitives.make_event(
        "resident.state_changed",
        subject_resident_id=subject_id,
        payload={
            "state_domain": state_domain,
            "previous_state": str(observed.get("previous_value") or ""),
            "state": state_value,
        },
        correlation=f"{source.id}:{occurrence}:{signature}",
    )
    scan.emit(source, event, f"state:{occurrence}:{signature}{_resident_suffix(source.subject)}",
              **_value_commit(locator, observed["previous_signature"], signature, state_value))


def _time_reached(scan: Scan, source: Source) -> None:
    """A scheduled time, a countdown or the next occurrence of a recurring schedule is due."""
    schedule_mode = str(source.config.get("schedule_mode") or "at")
    timer: dict[str, Any] = {}
    recurring: dict[str, Any] = {}
    if schedule_mode == "recurring":
        recurring = lineage_program_runtime.observe_recurring_schedule(scan.lineage_id, source.id, config=source.config)
        # Wake again when the next occurrence is due.
        if not source.exhausted:
            scan.wake_at(recurring.get("next_due_at"))
        if recurring.get("status") != "due":
            return
        scheduled_at = str(recurring.get("scheduled_at") or "")
        cursor = f"recurring:{int(recurring.get('generation') or 0)}:{scheduled_at}"
    elif schedule_mode == "countdown":
        timer = lineage_program_runtime.ensure_countdown_timer(
            scan.lineage_id, source.id, delay_seconds=int(source.config.get("delay_seconds") or 60))
        scheduled_at = str(timer.get("due_at") or "")
        cursor = f"timer:{int(timer.get('generation') or 0)}:{scheduled_at}"
    else:
        scheduled_at = str(source.config.get("scheduled_at") or "")
        cursor = f"time:{scheduled_at}"
    if not scheduled_at:
        return
    state = scan.runtime_state(source)
    # A one-time schedule fires once for its cursor; a recurring one fires per occurrence.
    recognized = schedule_mode != "recurring" and cursor in {
        *(state.get("consumed_cursors") or []), *(state.get("fired_cursors") or []),
    }
    if not scan.ports.scheduled_time_reached(scheduled_at):
        if not source.exhausted and not recognized and schedule_mode != "recurring":
            scan.wake_at(scheduled_at)
        return
    if recognized:
        return
    event = lineage_primitives.make_event(
        "time.reached",
        payload={
            "scheduled_at": scheduled_at,
            "schedule_mode": schedule_mode,
            **(
                {
                    "observed_at": str(recurring.get("observed_at") or ""),
                    "missed_count": int(recurring.get("missed_count") or 0),
                    "generation": int(recurring.get("generation") or 0),
                }
                if schedule_mode == "recurring"
                else {}
            ),
            **(
                {
                    "started_at": str(timer.get("started_at") or ""),
                    "delay_seconds": int(timer.get("delay_seconds") or 0),
                }
                if schedule_mode == "countdown"
                else {}
            ),
        },
        correlation=f"{source.id}:{scheduled_at}",
    )
    recurring_commit = (
        {"recurring_generation": int(recurring.get("generation") or 0), "recurring_scheduled_at": scheduled_at}
        if schedule_mode == "recurring"
        else {}
    )
    scan.emit(source, event, cursor, **recurring_commit)


# The system Event types a reconciliation pass observes, and who observes each.
OBSERVERS: dict[str, Callable[[Scan, Source], None]] = {
    lineage_release_events.EVENT: _release_published,
    "file.changed": _file_changed,
    "residents.all_finished": _residents_all_finished,
    lineage_message_events.MESSAGE_RECEIVED_EVENT: _message_received,
    "resident.work_started": _work_started,
    "resident.work_duration_exceeded": _work_duration_exceeded,
    "resident.queue_pressure_exceeded": _queue_pressure_exceeded,
    **{event_type: _value_changed for event_type in VALUE_READERS},
    "state.condition_met": _state_condition_met,
    "residents.all_idle": _residents_all_idle,
    "resident.context_pressure_exceeded": _context_pressure_exceeded,
    "resident.state_changed": _resident_state_changed,
    "time.reached": _time_reached,
}

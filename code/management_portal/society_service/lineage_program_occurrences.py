"""Event Source occurrences in the runtime: claims, the fire budget, fan-out settlement,
the claim token each subscriber keeps of an occurrence, and the per-source view.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from management_common.limit_contract import (
    limit_reached, limit_remaining, normalize_optional_limit,
)
from . import lineage_program_runtime
from .lineage_program_runtime import MAX_EVENT_CURSORS, MAX_EVENT_FIRE_CURSORS, _iso_now, _item_id


def claim_event(
    lineage_id: str,
    event_source_id: str,
    cursor: str,
) -> bool:
    """Atomically claim one source occurrence.

    The cursor is only a bounded dedupe token. It contains no reply,
    Workspace content, file body, Packet, or replayable snapshot.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_cursor = _item_id(cursor, field="event cursor")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        cursors = list(source.get("consumed_cursors") or [])
        if clean_cursor in cursors:
            return False
        cursors.append(clean_cursor)
        source["consumed_cursors"] = cursors[-MAX_EVENT_CURSORS:]
    return True


def claim_event_occurrence(
    lineage_id: str,
    event_source_id: str,
    cursor: str,
    *,
    max_fires: int | None = None,
) -> dict[str, Any]:
    """Claim one Event Source occurrence before it fans out to wires.

    Occurrence count belongs to the Event Source, not to its delivery routes.
    Every accepted fact increments it exactly once, including for an
    unlimited or unrouted source. Every subscriber route then receives the
    same claimed occurrence, so adding or removing output wires never changes
    the count. ``max_fires`` only decides whether a new fact may be accepted.

    ``fire_count`` remains the persisted scalar.
    Public runtime projections expose the same value as
    the semantic ``occurrence_count``.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_cursor = _item_id(cursor, field="Event fire cursor")
    limit = normalize_optional_limit(max_fires)
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {
                "consumed_cursors": [],
                "fired_cursors": [],
                "fire_count": 0,
                "fire_generation": 0,
            },
        )
        fired_cursors = list(source.get("fired_cursors") or [])
        count = max(0, int(source.get("fire_count") or 0))
        generation = max(0, int(source.get("fire_generation") or 0))
        if source.get("paused"):
            # A paused source produces no new Event (decision_68215b3a825b).
            return {
                "status": "paused",
                "accepted": False,
                "occurrence_count": count,
                "max_fires": limit,
                "remaining_fires": limit_remaining(limit, count),
                "generation": generation,
                "exhausted": False,
            }
        if clean_cursor in fired_cursors:
            return {
                "status": "duplicate",
                "accepted": True,
                "occurrence_count": count,
                "max_fires": limit,
                "remaining_fires": limit_remaining(limit, count),
                "generation": generation,
                "exhausted": limit_reached(limit, count),
            }
        if limit_reached(limit, count):
            return {
                "status": "exhausted",
                "accepted": False,
                "occurrence_count": count,
                "max_fires": limit,
                "remaining_fires": 0,
                "generation": generation,
                "exhausted": True,
            }
        fired_cursors.append(clean_cursor)
        source.pop("fanout_settled_generation", None)
        count += 1
        source["fired_cursors"] = fired_cursors[
            -MAX_EVENT_FIRE_CURSORS:
        ]
        source["fire_count"] = count
        source["fire_generation"] = generation
        source["last_fired_at"] = _iso_now()
        return {
            "status": "fired",
            "accepted": True,
            "occurrence_count": count,
            "max_fires": limit,
            "remaining_fires": limit_remaining(limit, count),
            "generation": generation,
            "exhausted": limit_reached(limit, count),
            "last_fired_at": source["last_fired_at"],
        }


def reset_event_source_fire_budget(
    lineage_id: str,
    event_source_id: str,
    *,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    """Re-arm one Event Source without replaying an old occurrence.

    Fired cursors remain as bounded dedupe evidence. File/state observation
    baselines are cleared so the next reconciliation starts from the current
    live value instead of emitting a change that happened while exhausted.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {
                "consumed_cursors": [],
                "fired_cursors": [],
                "fire_count": 0,
                "fire_generation": 0,
            },
        )
        generation = max(0, int(source.get("fire_generation") or 0))
        if (
            expected_generation is not None
            and generation != max(0, int(expected_generation))
        ):
            return {
                "status": "stale",
                "occurrence_count": max(
                    0,
                    int(source.get("fire_count") or 0),
                ),
                "generation": generation,
            }
        source["fire_count"] = 0
        source["fire_generation"] = generation + 1
        source.pop("last_fired_at", None)
        source.pop("last_delivery", None)
        source.pop("fanout_settled_generation", None)
        for field in (
            "file_locator",
            "file_signature",
            "file_generation",
            "state_locator",
            "state_signature",
            "state_value",
            "state_generation",
            "state_epoch",
            "completion_locator",
            "completion_message_id",
            "completion_at",
            "completion_generation",
            "timer_due_at",
            "timer_started_at",
            "timer_delay_seconds",
            "timer_generation",
        ):
            source.pop(field, None)
        return {
            "status": "reset",
            "occurrence_count": 0,
            "generation": generation + 1,
        }


def mark_event_fanout_complete(
    lineage_id: str,
    event_source_id: str,
    *,
    generation: int,
    occurrence_count: int,
) -> None:
    """Mark completed fanout without allowing an old dispatch to retire a reset source."""

    source_id = _item_id(event_source_id, field="event_source_id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if (
            source is not None
            and int(source.get("fire_generation") or 0) == generation
            and int(source.get("fire_count") or 0) == occurrence_count
        ):
            source["fanout_settled_generation"] = generation


def record_event_delivery_summary(
    lineage_id: str,
    event_source_id: str,
    deliveries: list[dict[str, Any]] | None,
    *,
    event_id: str = "",
    trigger_path_id: str = "",
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep the latest Event delivery outcome for the runtime UI and record this one."""

    source_id = _item_id(event_source_id, field="event_source_id")
    statuses: dict[str, int] = {}
    for item in deliveries or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown").strip() or "unknown"
        status = _item_id(status, field="delivery status")
        statuses[status] = statuses.get(status, 0) + 1
    queued_statuses = {"queued", "replaced"}
    dropped_statuses = {"dropped", "queue_full", "discarded"}
    failed_statuses = {
        "failed",
        "unavailable",
        "program_incomplete",
        "budget_full",
        "cycle_finishing",
    }
    summary = {
        "at": _iso_now(),
        "total_count": sum(statuses.values()),
        "delivered_count": sum(
            count
            for status, count in statuses.items()
            if status not in queued_statuses | dropped_statuses | failed_statuses
        ),
        "queued_count": sum(statuses.get(status, 0) for status in queued_statuses),
        "dropped_count": sum(statuses.get(status, 0) for status in dropped_statuses),
        "failed_count": sum(statuses.get(status, 0) for status in failed_statuses),
        "statuses": statuses,
    }
    if str(event_id or "").strip():
        summary["event_id"] = _item_id(
            event_id, field="Event delivery event id"
        )
    if str(trigger_path_id or "").strip():
        summary["trigger_path_id"] = _item_id(
            trigger_path_id, field="Event delivery Trigger Path id"
        )
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        source["last_delivery"] = summary
    return deepcopy(summary)


def _subscriber_cursor(
    cursor: str,
    subscriber_id: str,
    generation: int = 0,
) -> str:
    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id,
        field="event subscriber",
    )
    digest = hashlib.sha256(clean_cursor.encode("utf-8")).hexdigest()
    return (
        f"subscriber:{clean_subscriber_id}:generation:"
        f"{max(0, int(generation))}:{digest}"
    )


def _subscriber_cursor_matches_occurrence(
    token: object,
    cursor: str,
    subscriber_id: str,
) -> bool:
    """Match one durable occurrence across every lifecycle generation."""

    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id,
        field="event subscriber",
    )
    digest = hashlib.sha256(clean_cursor.encode("utf-8")).hexdigest()
    return (
        str(token).startswith(
            f"subscriber:{clean_subscriber_id}:generation:"
        )
        and str(token).endswith(f":{digest}")
    )


def _subscriber_occurrence_claimed(
    cursors: list[str],
    cursor: str,
    subscriber_id: str,
    generation: int | None,
) -> bool:
    if generation is not None:
        return _subscriber_cursor(
            cursor,
            subscriber_id,
            generation,
        ) in set(cursors)
    return any(
        _subscriber_cursor_matches_occurrence(
            token,
            cursor,
            subscriber_id,
        )
        for token in cursors
    )


def _fire_lifecycle_view(source: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """State, budget and latest fan-out of one Event Source."""

    maximum = normalize_optional_limit(config.get("max_fires"))
    count = max(0, int(source.get("fire_count") or 0))
    exhausted = limit_reached(maximum, count)
    paused = bool(source.get("paused"))
    return {
        "state": "paused" if paused else "exhausted" if exhausted else "listening",
        "paused": paused,
        "max_fires": maximum,
        "occurrence_count": count,
        "remaining_fires": limit_remaining(maximum, count),
        "generation": max(
            0,
            int(source.get("fire_generation") or 0),
        ),
        "last_fired_at": str(source.get("last_fired_at") or ""),
        "last_delivery": deepcopy(source.get("last_delivery") or {}),
        "exhausted": exhausted,
    }


def _recurring_schedule_view(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schedule_generation": max(
            0,
            int(source.get("generation") or 0),
        ),
        "last_scheduled_at": str(
            source.get("last_scheduled_at") or ""
        ),
        "next_scheduled_at": str(
            source.get("next_scheduled_at") or ""
        ),
        "next_due_at": str(
            source.get("next_due_at")
            or source.get("next_scheduled_at")
            or ""
        ),
        "schedule_pending": bool(
            source.get("pending_scheduled_at")
        ),
    }


def _upcoming_time_view(source: dict[str, Any], config: dict[str, Any], exhausted: bool) -> dict[str, Any]:
    """The one pending time of a one-shot or countdown source, until it fires."""

    recognized = {*(source.get("consumed_cursors") or []), *(source.get("fired_cursors") or [])}
    if str(config.get("schedule_mode") or "at") == "countdown":
        upcoming = str(source.get("timer_due_at") or "")
        cursor = f"timer:{int(source.get('timer_generation') or 0)}:{upcoming}"
    else:
        upcoming = str(config.get("scheduled_at") or "")
        cursor = f"time:{upcoming}"
    if cursor in recognized or exhausted:
        upcoming = ""
    return {"next_scheduled_at": upcoming, "next_due_at": upcoming}


def _all_idle_view(source: dict[str, Any]) -> dict[str, Any]:
    observed_value = str(source.get("state_value") or "")
    return {
        "baseline_established": bool(observed_value),
        "running_count": (
            max(0, int(observed_value))
            if observed_value
            and observed_value.isdigit()
            else None
        ),
    }


def _context_pressure_view(source: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    observed_value = str(source.get("state_value") or "")
    try:
        used_percent = (
            round(float(observed_value), 1)
            if observed_value
            else None
        )
    except (TypeError, ValueError):
        used_percent = None
    try:
        threshold_percent = round(
            float(
                config.get("threshold_percent")
                if config.get("threshold_percent") is not None
                else 85
            ),
            1,
        )
    except (TypeError, ValueError):
        threshold_percent = 85.0
    return {
        "baseline_established": used_percent is not None,
        "used_percent": used_percent,
        "threshold_percent": threshold_percent,
        "exceeded": (
            used_percent is not None
            and used_percent > threshold_percent
        ),
    }


# Event types whose observed value is a predicate ("true" / "false").
_PREDICATE_EVENT_TYPES = frozenset({
    "resident.work_duration_exceeded",
    "resident.queue_pressure_exceeded",
    "state.condition_met",
})


def _predicate_view(source: dict[str, Any], config: dict[str, Any], event_type: str) -> dict[str, Any]:
    """Whether the condition holds, and the settings it is checked with."""

    observed_value = str(source.get("state_value") or "")
    predicate_value = (
        observed_value == "true"
        if observed_value in {"true", "false"}
        else None
    )
    projection: dict[str, Any] = {
        "baseline_established": predicate_value is not None,
        "condition_met": predicate_value,
    }
    if event_type == "resident.work_duration_exceeded":
        projection["duration_seconds"] = max(
            1,
            int(config.get("duration_seconds") or 900),
        )
    elif event_type == "resident.queue_pressure_exceeded":
        metric = str(
            config.get("pressure_metric") or "queue_length"
        )
        projection["pressure_metric"] = metric
        projection["pressure_threshold"] = max(
            1,
            int(
                config.get("wait_seconds_threshold") or 300
                if metric == "oldest_wait_seconds"
                else config.get("queue_length_threshold") or 3
            ),
        )
    else:
        projection["key"] = str(config.get("key") or "")
        projection["operator"] = str(
            config.get("operator") or "equals"
        )
    return projection


def event_source_runtime_view(
    lineage_id: str,
    source_configs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return Event Source fire lifecycle independently from graph state."""

    runtime = lineage_program_runtime.read_runtime(lineage_id)
    states = runtime.get("event_sources") or {}
    result: dict[str, dict[str, Any]] = {}
    for raw_source_id, raw_config in source_configs.items():
        source_id = _item_id(raw_source_id, field="event_source_id")
        config = raw_config if isinstance(raw_config, dict) else {}
        source = states.get(source_id) or {}
        event_type = str(config.get("event_type") or "")
        projection = _fire_lifecycle_view(source, config)
        if event_type == "time.reached" and str(config.get("schedule_mode") or "") == "recurring":
            projection.update(_recurring_schedule_view(source))
        if event_type == "time.reached" and str(config.get("schedule_mode") or "at") in {"at", "countdown"}:
            projection.update(_upcoming_time_view(source, config, projection["exhausted"]))
        if event_type == "residents.all_idle":
            projection.update(_all_idle_view(source))
        if event_type == "resident.context_pressure_exceeded":
            projection.update(_context_pressure_view(source, config))
        if event_type in _PREDICATE_EVENT_TYPES:
            projection.update(_predicate_view(source, config, event_type))
        result[source_id] = projection
    return result


def forget_event_source(
    lineage_id: str,
    event_source_id: str,
    *,
    preserve_pause: bool = False,
) -> None:
    source_id = _item_id(event_source_id, field="event_source_id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        previous = runtime["event_sources"].pop(source_id, None)
        if preserve_pause and isinstance(previous, dict) and previous.get("paused"):
            runtime["event_sources"][source_id] = {
                "consumed_cursors": [], "paused": True, "paused_at": previous.get("paused_at", ""),
            }


def fence_event_source_contract_preserving_fire_budget(
    lineage_id: str,
    event_source_id: str,
) -> None:
    """Fence stale subscriptions while retaining the source-wide fire count."""

    source_id = _item_id(event_source_id, field="event_source_id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if not isinstance(source, dict):
            return
        preserved = {
            key: deepcopy(source[key])
            for key in (
                "paused",
                "paused_at",
                "rebaseline",
                "fired_cursors",
                "fire_count",
                "fire_generation",
                "last_fired_at",
                "recurring_contract",
                "generation",
                "last_scheduled_at",
                "next_scheduled_at",
                "next_due_at",
                "next_missed_count",
                "pending_scheduled_at",
                "pending_due_at",
                "pending_next_scheduled_at",
                "pending_next_due_at",
                "pending_observed_at",
                "pending_missed_count",
                "pending_generation",
            )
            if key in source
        }
        if not preserved:
            runtime["event_sources"].pop(source_id, None)
        else:
            runtime["event_sources"][source_id] = {
                "consumed_cursors": [],
                **preserved,
            }

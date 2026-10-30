"""Evaluate and persist edge-triggered Event conditions.

The graph orchestrator owns when to observe; this module owns condition policy.
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from . import lineage_program_observations
from .lineage_recurring_schedule import parse_utc


def _scheduled_time_reached(value: object, *, now: datetime | None = None) -> bool:
    parsed = parse_utc(value)
    return parsed is not None and parsed <= (now or datetime.now(timezone.utc))


def _elapsed_whole_seconds(
    started_at: object,
    captured_at: object,
) -> int | None:
    started = parse_utc(started_at)
    captured = parse_utc(captured_at)
    if started is None or captured is None:
        return None
    return max(0, int((captured - started).total_seconds()))


def _state_condition_matches(
    *,
    existed: bool,
    value: object,
    operator: str,
    expected_value: object,
) -> bool:
    if operator == "exists":
        return existed
    if operator == "missing":
        return not existed
    if not existed:
        return False
    if operator == "equals":
        return value == expected_value
    if operator == "not_equals":
        return value != expected_value
    numeric_types = (int, float)
    if (
        isinstance(value, bool)
        or isinstance(expected_value, bool)
        or not isinstance(value, numeric_types)
        or not isinstance(expected_value, numeric_types)
        or not math.isfinite(float(value))
        or not math.isfinite(float(expected_value))
    ):
        return False
    if operator == "greater_than":
        return value > expected_value
    if operator == "greater_or_equal":
        return value >= expected_value
    if operator == "less_than":
        return value < expected_value
    if operator == "less_or_equal":
        return value <= expected_value
    return False


def _resident_activity_event_sources(
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        source
        for source in (plan.get("event_sources") or {}).values()
        if (
            isinstance(source, dict)
            and str(source.get("event_type") or "")
            in {
                "residents.all_idle",
                "resident.work_started",
                "resident.work_duration_exceeded",
                "resident.queue_pressure_exceeded",
            }
        )
    ]


def _resident_context_pressure_event_sources(
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        source
        for source in (plan.get("event_sources") or {}).values()
        if (
            isinstance(source, dict)
            and str(source.get("event_type") or "")
            == "resident.context_pressure_exceeded"
        )
    ]


def _graph_state_condition_event_sources(
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        source
        for source in (plan.get("event_sources") or {}).values()
        if (
            isinstance(source, dict)
            and str(source.get("event_type") or "")
            in {"state.condition_met", "state.changed"}
        )
    ]


def _observe_boolean_event_condition(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    matched: bool,
    fire_on_initial_match: bool,
    subject: str = "",
) -> dict[str, Any]:
    """Observe one edge-triggered predicate without persisting its subject.

    ``subject`` keeps the predicate per Resident for a source that watches several.
    """

    value = "true" if matched else "false"
    signature = "sha256:" + hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()
    observed = lineage_program_observations.observe_event_source_value(
        lineage_id,
        event_source_id,
        locator=locator,
        signature=signature,
        value=value,
        subject=subject,
    )
    status = str(observed.get("status") or "")
    previous_matched = (
        str(observed.get("previous_value") or "") == "true"
        if status == "changed"
        else None
    )
    should_emit = (
        status == "baseline"
        and matched
        and fire_on_initial_match
        and not observed.get("resumed")
    ) or (
        status == "changed"
        and previous_matched is False
        and matched
    )
    return {
        "observed": observed,
        "status": status,
        "previous_matched": previous_matched,
        "matched": matched,
        "should_emit": should_emit,
        "should_commit": status == "changed" and not should_emit,
        "locator": locator,
        "signature": signature,
        "value": value,
        "subject": subject,
    }


def _commit_boolean_event_condition(
    lineage_id: str,
    event_source_id: str,
    condition: dict[str, Any],
) -> bool:
    observed = condition["observed"]
    return lineage_program_observations.commit_event_source_value(
        lineage_id,
        event_source_id,
        locator=str(condition["locator"]),
        previous_signature=str(observed["previous_signature"]),
        signature=str(condition["signature"]),
        value=str(condition["value"]),
        subject=str(condition.get("subject") or ""),
    )


def _boolean_condition_pending_state(
    condition: dict[str, Any],
) -> dict[str, str]:
    if condition["status"] != "changed":
        return {}
    observed = condition["observed"]
    return {
        "state_locator": str(condition["locator"]),
        "previous_state_signature": str(
            observed["previous_signature"]
        ),
        "state_signature": str(condition["signature"]),
        "state_value": str(condition["value"]),
        **({"state_subject": str(condition["subject"])} if condition.get("subject") else {}),
    }

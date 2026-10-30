"""Time sources in the runtime: countdown timers and recurring schedules."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from . import lineage_program_runtime, lineage_recurring_schedule
from .lineage_program_runtime import _item_id, _utc_now


def ensure_countdown_timer(
    lineage_id: str,
    event_source_id: str,
    *,
    delay_seconds: int,
) -> dict[str, Any]:
    """Anchor one countdown in runtime so graph refreshes cannot restart it."""

    source_id = _item_id(event_source_id, field="event_source_id")
    delay = max(1, min(7 * 24 * 60 * 60, int(delay_seconds or 1)))
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        previous_delay = max(
            0,
            int(source.get("timer_delay_seconds") or 0),
        )
        due_at = lineage_recurring_schedule.parse_utc(source.get("timer_due_at"))
        started_at = lineage_recurring_schedule.parse_utc(source.get("timer_started_at"))
        generation = max(
            0,
            int(source.get("timer_generation") or 0),
        )
        if due_at is None or started_at is None or previous_delay != delay:
            started_at = _utc_now()
            due_at = started_at + timedelta(seconds=delay)
            generation += 1
            source["timer_started_at"] = (
                started_at.isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            source["timer_due_at"] = (
                due_at.isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            source["timer_delay_seconds"] = delay
            source["timer_generation"] = generation
        return {
            "started_at": str(source.get("timer_started_at") or ""),
            "due_at": str(source.get("timer_due_at") or ""),
            "delay_seconds": delay,
            "generation": generation,
            "reached": bool(
                due_at is not None
                and due_at <= _utc_now()
            ),
        }


def _paused_schedule(source: dict[str, Any]) -> dict[str, Any]:
    """What a paused recurring source reports: its schedule, without a due occurrence."""

    return {
        "status": "paused",
        "generation": max(0, int(source.get("generation") or 0)),
        "next_scheduled_at": str(source.get("next_scheduled_at") or ""),
        "next_due_at": str(source.get("next_due_at") or ""),
    }


def _current_schedule(
    source: dict[str, Any],
    normalized: dict[str, Any],
    contract: str,
    now: datetime,
) -> tuple[int, datetime | None]:
    """The source's schedule generation and next time; a changed schedule starts again after ``now``."""

    previous_contract = str(source.get("recurring_contract") or "")
    generation = max(0, int(source.get("generation") or 0))
    next_at = lineage_recurring_schedule.parse_utc(
        source.get("next_scheduled_at")
    )
    changed = previous_contract != contract or next_at is None
    if changed:
        generation += 1
        next_at = lineage_recurring_schedule.first_after(normalized, now)
        source["recurring_contract"] = contract
        source["generation"] = generation
        source["next_scheduled_at"] = (
            lineage_recurring_schedule.iso_utc(next_at)
        )
        source["next_due_at"] = lineage_recurring_schedule.iso_utc(
            lineage_recurring_schedule.sample_due_at(normalized, next_at)
        )
        source.pop("last_scheduled_at", None)
        for field in (
            "next_missed_count",
            "pending_scheduled_at",
            "pending_due_at",
            "pending_next_scheduled_at",
            "pending_next_due_at",
            "pending_observed_at",
            "pending_missed_count",
            "pending_generation",
        ):
            source.pop(field, None)
    return generation, next_at


def _pending_occurrence(source: dict[str, Any], generation: int) -> dict[str, Any] | None:
    """The occurrence persisted before its dispatch, retried as it was; None when none is pending."""

    pending_at = str(source.get("pending_scheduled_at") or "")
    pending_generation = max(
        0,
        int(source.get("pending_generation") or 0),
    )
    if not (pending_at and pending_generation == generation):
        return None
    return {
        "status": "due",
        "scheduled_at": pending_at,
        "due_at": str(source.get("pending_due_at") or ""),
        "observed_at": str(source.get("pending_observed_at") or ""),
        "missed_count": max(
            0,
            int(source.get("pending_missed_count") or 0),
        ),
        "generation": generation,
        "next_scheduled_at": str(
            source.get("pending_next_scheduled_at") or ""
        ),
        "next_due_at": str(
            source.get("pending_next_due_at")
            or ""
        ),
    }


def _waiting_schedule(source: dict[str, Any], generation: int) -> dict[str, Any]:
    return {
        "status": "waiting",
        "last_scheduled_at": str(
            source.get("last_scheduled_at") or ""
        ),
        "next_scheduled_at": str(source["next_scheduled_at"]),
        "next_due_at": str(source["next_due_at"]),
        "generation": generation,
    }


def _due_windows(
    source: dict[str, Any],
    normalized: dict[str, Any],
    next_at: datetime,
    now: datetime,
) -> tuple[datetime, int, datetime, int]:
    """(latest due window, how many are due, the window after it, missed windows already carried)."""

    carried_missed = max(0, int(source.get("next_missed_count") or 0))
    if carried_missed:
        # A coalesced candidate was already selected and its due time
        # persisted. Fire it before considering newer nominal windows.
        latest = next_at
        due_count = carried_missed + 1
        future = lineage_recurring_schedule.first_after(normalized, latest)
    else:
        latest, due_count, future = lineage_recurring_schedule.due_window(
            normalized,
            next_at,
            now,
        )
    return latest, due_count, future, carried_missed


def _skip_missed(
    source: dict[str, Any],
    normalized: dict[str, Any],
    next_at: datetime,
    latest: datetime,
    due_count: int,
    future: datetime,
    now: datetime,
    generation: int,
) -> dict[str, Any]:
    """``skip``: advance past missed windows without an Event."""

    latest_due = latest
    if latest + timedelta(seconds=normalized["jitter_seconds"]) > now:
        latest_due = lineage_recurring_schedule.sample_due_at(
            normalized, latest
        )
    if latest_due > now:
        previous, skipped_count, _ = lineage_recurring_schedule.due_window(
            normalized,
            next_at,
            latest - timedelta(microseconds=1),
        )
        source["last_scheduled_at"] = lineage_recurring_schedule.iso_utc(previous)
        source["next_scheduled_at"] = lineage_recurring_schedule.iso_utc(latest)
        source["next_due_at"] = lineage_recurring_schedule.iso_utc(latest_due)
        return {
            "status": "skipped",
            "skipped_count": skipped_count,
            "last_scheduled_at": source["last_scheduled_at"],
            "next_scheduled_at": source["next_scheduled_at"],
            "generation": generation,
        }
    source["last_scheduled_at"] = (
        lineage_recurring_schedule.iso_utc(latest)
    )
    source["next_scheduled_at"] = (
        lineage_recurring_schedule.iso_utc(future)
    )
    source["next_due_at"] = lineage_recurring_schedule.iso_utc(
        lineage_recurring_schedule.sample_due_at(normalized, future)
    )
    source.pop("next_missed_count", None)
    return {
        "status": "skipped",
        "skipped_count": due_count,
        "last_scheduled_at": source["last_scheduled_at"],
        "next_scheduled_at": source["next_scheduled_at"],
        "generation": generation,
    }


def _coalesce_missed(
    source: dict[str, Any],
    normalized: dict[str, Any],
    latest: datetime,
    due_count: int,
    now: datetime,
    generation: int,
) -> dict[str, Any] | None:
    """``coalesce_latest``: one occurrence stands for the missed windows; waiting until it is due."""

    # Coalesce nominal windows first, then sample and persist the
    # selected occurrence's actual due time. A later poll must reuse
    # that sample even when another nominal window has begun.
    next_due = lineage_recurring_schedule.sample_due_at(normalized, latest)
    source["next_scheduled_at"] = lineage_recurring_schedule.iso_utc(latest)
    source["next_due_at"] = lineage_recurring_schedule.iso_utc(next_due)
    source["next_missed_count"] = due_count - 1
    if next_due > now:
        return _waiting_schedule(source, generation)
    return None


def _stage_occurrence(
    source: dict[str, Any],
    normalized: dict[str, Any],
    latest: datetime,
    future: datetime,
    due_count: int,
    now: datetime,
    generation: int,
) -> dict[str, Any]:
    """Persist the due occurrence before its dispatch, so a restart retries the same one."""

    scheduled_at = lineage_recurring_schedule.iso_utc(latest)
    due_at = str(source["next_due_at"])
    observed_at = lineage_recurring_schedule.iso_utc(now)
    next_scheduled_at = lineage_recurring_schedule.iso_utc(future)
    next_due_at = lineage_recurring_schedule.iso_utc(
        lineage_recurring_schedule.sample_due_at(normalized, future)
    )
    source.update(
        {
            "pending_scheduled_at": scheduled_at,
            "pending_due_at": due_at,
            "pending_next_scheduled_at": next_scheduled_at,
            "pending_next_due_at": next_due_at,
            "pending_observed_at": observed_at,
            "pending_missed_count": due_count - 1,
            "pending_generation": generation,
        }
    )
    return {
        "status": "due",
        "scheduled_at": scheduled_at,
        "due_at": due_at,
        "observed_at": observed_at,
        "missed_count": due_count - 1,
        "generation": generation,
        "next_scheduled_at": next_scheduled_at,
        "next_due_at": next_due_at,
    }


def observe_recurring_schedule(
    lineage_id: str,
    event_source_id: str,
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Persist and observe one recurring schedule without replay ambiguity.

    At most one occurrence is pending at the source boundary. Its payload
    metadata is persisted before dispatch, so a restart retries the same
    generation/cursor. ``skip`` advances missed windows without producing an
    Event; ``coalesce_latest`` represents them with the latest due occurrence.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    normalized = lineage_recurring_schedule.normalize_config(config)
    paused_source = lineage_program_runtime._read_object(lineage_id, "event_sources", source_id) or {}
    if paused_source.get("paused"):
        # A paused source skips due fires; resuming continues from the next
        # scheduled time without replaying the paused window.
        return _paused_schedule(paused_source)
    contract = lineage_recurring_schedule.fingerprint(normalized)
    now = _utc_now()
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        generation, next_at = _current_schedule(source, normalized, contract, now)
        pending = _pending_occurrence(source, generation)
        if pending is not None:
            return pending

        assert next_at is not None
        next_due = lineage_recurring_schedule.parse_utc(source.get("next_due_at"))
        assert next_due is not None
        if next_due > now:
            return _waiting_schedule(source, generation)
        latest, due_count, future, carried_missed = _due_windows(source, normalized, next_at, now)
        if normalized["misfire_policy"] == "skip" and due_count > 1:
            return _skip_missed(source, normalized, next_at, latest, due_count, future, now, generation)
        if due_count > 1 and not carried_missed:
            waiting = _coalesce_missed(source, normalized, latest, due_count, now, generation)
            if waiting is not None:
                return waiting
        return _stage_occurrence(source, normalized, latest, future, due_count, now, generation)


def commit_recurring_schedule(
    lineage_id: str,
    event_source_id: str,
    *,
    generation: int,
    scheduled_at: str,
) -> dict[str, Any]:
    """Advance one pending occurrence, fenced by schedule generation."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_scheduled_at = _item_id(scheduled_at, field="scheduled_at")
    expected_generation = max(0, int(generation))
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id) or {}
        if (
            max(0, int(source.get("generation") or 0))
            != expected_generation
            or str(source.get("pending_scheduled_at") or "")
            != clean_scheduled_at
            or max(0, int(source.get("pending_generation") or 0))
            != expected_generation
        ):
            return {"status": "stale", "generation": max(
                0, int(source.get("generation") or 0)
            )}
        source["last_scheduled_at"] = clean_scheduled_at
        source["next_scheduled_at"] = str(
            source.get("pending_next_scheduled_at") or ""
        )
        source["next_due_at"] = str(
            source.get("pending_next_due_at") or ""
        )
        source.pop("next_missed_count", None)
        for field in (
            "pending_scheduled_at",
            "pending_due_at",
            "pending_next_scheduled_at",
            "pending_next_due_at",
            "pending_observed_at",
            "pending_missed_count",
            "pending_generation",
        ):
            source.pop(field, None)
        return {
            "status": "advanced",
            "generation": expected_generation,
            "last_scheduled_at": source["last_scheduled_at"],
            "next_scheduled_at": source["next_scheduled_at"],
            "next_due_at": source["next_due_at"],
        }

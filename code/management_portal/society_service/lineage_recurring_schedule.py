from __future__ import annotations

import calendar
import hashlib
import json
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(config.get("recurrence_kind") or "interval").strip().lower()
    if kind not in {"interval", "calendar"}:
        raise ValueError("周期计划类型必须是 interval 或 calendar")
    misfire = str(
        config.get("misfire_policy") or "coalesce_latest"
    ).strip().lower()
    if misfire not in {"skip", "coalesce_latest"}:
        raise ValueError("misfire_policy 必须是 skip 或 coalesce_latest")
    overlap = str(config.get("overlap_policy") or "drop").strip().lower()
    if overlap not in {"drop", "queue_one", "latest"}:
        raise ValueError("overlap_policy 必须是 drop、queue_one 或 latest")
    result: dict[str, Any] = {
        "recurrence_kind": kind,
        "misfire_policy": misfire,
        "overlap_policy": overlap,
    }
    raw_jitter = config.get("jitter_seconds", 0)
    if raw_jitter is None or raw_jitter == "":
        raw_jitter = 0
    if isinstance(raw_jitter, (bool, float)):
        raise ValueError("jitter_seconds 必须是整数")
    try:
        jitter_seconds = int(raw_jitter)
    except (TypeError, ValueError) as exc:
        raise ValueError("jitter_seconds 必须是整数") from exc
    if not 0 <= jitter_seconds <= 86_400:
        raise ValueError("jitter_seconds 必须在 0 到 86400 之间")
    result["jitter_seconds"] = jitter_seconds
    if kind == "interval":
        try:
            seconds = int(config.get("interval_seconds") or 60)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval_seconds 必须是整数") from exc
        if not 1 <= seconds <= 31_536_000:
            raise ValueError("interval_seconds 必须在 1 到 31536000 之间")
        if jitter_seconds >= seconds:
            raise ValueError("jitter_seconds 必须小于 interval_seconds")
        result["interval_seconds"] = seconds
        return result

    timezone_name = str(config.get("timezone") or "UTC").strip() or "UTC"
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone 必须是有效的 IANA 时区") from exc
    frequency = str(
        config.get("calendar_frequency") or "daily"
    ).strip().lower()
    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("calendar_frequency 必须是 daily、weekly 或 monthly")
    time_text = str(config.get("calendar_time") or "09:00").strip()
    try:
        parsed_time = time.fromisoformat(time_text)
    except ValueError as exc:
        raise ValueError("calendar_time 必须是 HH:MM 或 HH:MM:SS") from exc
    if parsed_time.tzinfo is not None:
        raise ValueError("calendar_time 不应包含时区")
    result.update(
        {
            "timezone": timezone_name,
            "calendar_frequency": frequency,
            "calendar_time": parsed_time.isoformat(timespec="seconds"),
        }
    )
    if frequency == "weekly":
        raw_weekdays = config.get("calendar_weekdays") or "mon"
        values = (
            raw_weekdays
            if isinstance(raw_weekdays, list)
            else str(raw_weekdays).split(",")
        )
        weekdays: list[str] = []
        for raw in values:
            value = str(raw).strip().lower()[:3]
            if value not in WEEKDAYS:
                raise ValueError(
                    "calendar_weekdays 只能包含 mon..sun，以逗号分隔"
                )
            if value not in weekdays:
                weekdays.append(value)
        if not weekdays:
            raise ValueError("weekly 计划至少需要一个 weekday")
        result["calendar_weekdays"] = weekdays
    if frequency == "monthly":
        try:
            day = int(config.get("calendar_day") or 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("calendar_day 必须是整数") from exc
        if not 1 <= day <= 31:
            raise ValueError("calendar_day 必须在 1 到 31 之间")
        result["calendar_day"] = day
    return result


def fingerprint(config: Mapping[str, Any]) -> str:
    normalized = normalize_config(config)
    # The implicit zero delay must keep existing schedules on their current
    # generation when this field is introduced.
    if not normalized["jitter_seconds"]:
        normalized.pop("jitter_seconds")
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sample_due_at(config: Mapping[str, Any], scheduled_at: datetime) -> datetime:
    """Sample one occurrence's actual due time before persisting it."""
    maximum = int(config["jitter_seconds"])
    return scheduled_at + timedelta(
        seconds=secrets.randbelow(maximum + 1) if maximum else 0
    )


def first_after(config: Mapping[str, Any], after: datetime) -> datetime:
    normalized = normalize_config(config)
    current = after.astimezone(timezone.utc)
    if normalized["recurrence_kind"] == "interval":
        return current + timedelta(seconds=normalized["interval_seconds"])
    return _next_calendar_after(normalized, current)


def due_window(
    config: Mapping[str, Any],
    first_due: datetime,
    observed_at: datetime,
) -> tuple[datetime, int, datetime]:
    """Return latest due occurrence, due count, and first future occurrence."""

    normalized = normalize_config(config)
    due = first_due.astimezone(timezone.utc)
    observed = observed_at.astimezone(timezone.utc)
    if due > observed:
        return due, 0, due
    if normalized["recurrence_kind"] == "interval":
        interval = int(normalized["interval_seconds"])
        count = int((observed - due).total_seconds() // interval) + 1
        latest = due + timedelta(seconds=interval * (count - 1))
        return latest, count, latest + timedelta(seconds=interval)

    latest = due
    count = 1
    future = _next_calendar_after(normalized, latest)
    while future <= observed:
        latest = future
        count += 1
        if count > 100_000:
            raise RuntimeError("calendar schedule missed-occurrence scan is too large")
        future = _next_calendar_after(normalized, latest)
    return latest, count, future


def _next_calendar_after(
    config: Mapping[str, Any],
    after_utc: datetime,
) -> datetime:
    zone = ZoneInfo(str(config["timezone"]))
    local_after = after_utc.astimezone(zone)
    wall_time = time.fromisoformat(str(config["calendar_time"]))
    frequency = str(config["calendar_frequency"])
    candidate_date = local_after.date()
    for offset in range(0, 366 * 20):
        day = candidate_date + timedelta(days=offset)
        if not _calendar_date_matches(config, frequency, day):
            continue
        candidate = _valid_local_occurrence(day, wall_time, zone)
        if candidate is not None and candidate > after_utc:
            return candidate
    raise RuntimeError("cannot find the next calendar occurrence")


def _calendar_date_matches(
    config: Mapping[str, Any],
    frequency: str,
    candidate: date,
) -> bool:
    if frequency == "daily":
        return True
    if frequency == "weekly":
        allowed = {WEEKDAYS[value] for value in config["calendar_weekdays"]}
        return candidate.weekday() in allowed
    requested = int(config["calendar_day"])
    return (
        requested <= calendar.monthrange(candidate.year, candidate.month)[1]
        and candidate.day == requested
    )


def _valid_local_occurrence(
    day: date,
    wall_time: time,
    zone: ZoneInfo,
) -> datetime | None:
    # Round-trip through UTC. Nonexistent local times (DST spring-forward) do
    # not round-trip and are skipped. Ambiguous times choose the earliest
    # actual instant deterministically.
    valid: list[datetime] = []
    naive = datetime.combine(day, wall_time)
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(timezone.utc)
        round_trip = utc.astimezone(zone)
        if (
            round_trip.replace(tzinfo=None) == naive
            and round_trip.fold == fold
            and utc not in valid
        ):
            valid.append(utc)
    return min(valid) if valid else None

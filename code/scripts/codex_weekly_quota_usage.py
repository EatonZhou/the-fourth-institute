#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_common.format_tags import schema_matches
from management_common.portal_paths import path as portal_path
from management_portal.shared_state import atomic_replace_json  # noqa: E402


from management_common.provider_account_paths import provider_root
DEFAULT_CODEX_DIRECTORY = provider_root("codex")
DEFAULT_OUTPUT_ROOT = portal_path("staging_root") / "shared/codex_weekly_usage"
DEFAULT_DAILY_SNAPSHOT_ROOT = portal_path("staging_root") / "shared/codex_limit_usage/daily"
DEFAULT_TIMEZONE = "Asia/Hong_Kong"
DEFAULT_DISPLAY_YEAR = 2026
PIPELINE_SCHEMA = "tfi.codex-usage-pipeline.v1"
FORK_REPLAY_BURST_SECONDS = 1.0
RESET_AT_EQUIVALENCE_SECONDS = 60
# Codex has used both layouts over time: older records usually store the
# 5h window in primary and the weekly window in secondary; newer records may
# store the weekly window in primary with no secondary window. Select by
# window_minutes instead of by primary/secondary position.
WEEKLY_WINDOW_MINUTES = 10080
TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
DAILY_ESTIMATE_MAX_WINDOW_DAYS = 3
DAILY_ESTIMATE_MIN_WINDOW_DAYS = 2
DAILY_ESTIMATE_MIN_USAGE_PERCENT = 15.0
DAILY_ESTIMATE_MAX_ALIGNMENT_LAG_MINUTES = 30.0
DAILY_SNAPSHOT_MAX_BOUNDARY_LAG_MINUTES = 30.0

# Standard API prices from https://developers.openai.com/api/docs/pricing,
# in USD per 1M tokens. The table below uses the Standard short-context rows.
API_PRICE_PER_1M = {
    "gpt-6-astra": {"input": 10.00, "cached_input": 1.00, "output": 50.00},
    "gpt-5.6-sol": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.6-luna": {"input": 1.00, "cached_input": 0.10, "output": 6.00},
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.3-codex": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
}
MODEL_PRICE_ALIASES = {
    "gpt-5.3-codex-spark": "gpt-5.3-codex",
}
PRICING_SOURCE_URL = "https://developers.openai.com/api/docs/pricing"


@dataclass
class WeeklyEvent:
    timestamp: datetime
    session_id: str
    model: str
    model_provider: str
    plan_type: str
    limit_source: str
    reset_at: int
    used_percent: float | None
    usage: dict[str, int]
    source_path: str = ""
    source_line: int = 0


@dataclass
class SessionActivityEvent:
    timestamp: datetime
    session_id: str
    kind: str


@dataclass(frozen=True)
class DailyLimitSnapshot:
    boundary: datetime
    observed_at: datetime
    account_id: str
    status: str
    stale: bool
    used_percent: float | None
    reset_at: int
    source_path: str


@dataclass
class ResetBucket:
    reset_at: int
    events: list[WeeklyEvent] = field(default_factory=list)

    @property
    def observed_count(self) -> int:
        return len(self.events)

    @property
    def first_event(self) -> datetime | None:
        return min((event.timestamp for event in self.events), default=None)

    @property
    def last_event(self) -> datetime | None:
        return max((event.timestamp for event in self.events), default=None)

    @property
    def max_used_percent(self) -> float | None:
        values = [event.used_percent for event in self.events if event.used_percent is not None]
        return max(values) if values else None

    @property
    def last_used_percent(self) -> float | None:
        if not self.events:
            return None
        last = max(self.events, key=lambda event: event.timestamp)
        return last.used_percent


@dataclass
class InferredReset:
    event_at: datetime
    reset_kind: str
    to_reset_at: int
    from_reset_at: int | None = None
    before_used_percent: float | None = None
    after_used_percent: float | None = None
    observed_count: int = 0
    session_count: int = 0
    evidence: str = ""


def _read_json_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except ValueError:  # JSONDecodeError and UnicodeDecodeError: a torn or binary line
        return None
    return payload if isinstance(payload, dict) else None


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _clean_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {key: _nonnegative_int(value.get(key)) for key in TOKEN_KEYS if _nonnegative_int(value.get(key))}


def _add_usage(left: dict[str, int], right: dict[str, int]) -> None:
    for key in TOKEN_KEYS:
        value = _nonnegative_int(right.get(key))
        if value:
            left[key] = left.get(key, 0) + value


def _weekly_limit_from_rate_limits(rate_limits: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    matches = []
    for source in ("primary", "secondary"):
        value = rate_limits.get(source)
        if not isinstance(value, dict):
            continue
        if _nonnegative_int(value.get("window_minutes")) == WEEKLY_WINDOW_MINUTES:
            matches.append((source, value))
    if not matches:
        return None
    return matches[0]


def _rollout_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    try:
        return sorted(path for path in root.rglob("*.jsonl") if path.is_file())
    except OSError:
        return []


def _date_arg(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid date {raw!r}, expected YYYY-MM-DD") from exc


def _event_from_token_count(
    obj: dict[str, Any],
    *,
    session_id: str,
    model: str,
    model_provider: str,
    source_path: str,
    source_line: int,
    plan_type_filter: str,
    since_utc: datetime,
    until_utc: datetime,
    max_reset_lag_seconds: int,
) -> tuple[WeeklyEvent | None, str]:
    timestamp = _parse_timestamp(obj.get("timestamp"))
    if timestamp is None or timestamp < since_utc or timestamp >= until_utc:
        return None, "outside_range"

    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    if obj.get("type") != "event_msg" or payload.get("type") != "token_count":
        return None, "not_token_count"

    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    usage = _clean_usage(info.get("last_token_usage"))
    if not usage:
        return None, "missing_usage"

    rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    weekly_limit = _weekly_limit_from_rate_limits(rate_limits)
    if weekly_limit is None:
        return None, "missing_weekly_limit"
    limit_source, weekly = weekly_limit

    reset_at = _nonnegative_int(weekly.get("resets_at"))
    if not reset_at:
        return None, "missing_reset_at"
    reset_time = datetime.fromtimestamp(reset_at, timezone.utc)
    lag_seconds = (reset_time - timestamp).total_seconds()
    if lag_seconds < -60:
        return None, "past_reset_at"
    if lag_seconds > max_reset_lag_seconds:
        return None, "far_reset_at"

    plan_type = str(rate_limits.get("plan_type") or "")
    if plan_type_filter and plan_type != plan_type_filter:
        return None, "plan_filtered"

    return WeeklyEvent(
        timestamp=timestamp,
        session_id=session_id,
        model=model,
        model_provider=model_provider,
        plan_type=plan_type,
        limit_source=limit_source,
        reset_at=reset_at,
        used_percent=_float_or_none(weekly.get("used_percent")),
        usage=usage,
        source_path=source_path,
        source_line=source_line,
    ), "accepted"


def collect_events(
    sessions_root: Path,
    *,
    since_utc: datetime,
    until_utc: datetime,
    plan_type_filter: str,
    max_reset_lag_seconds: int,
) -> tuple[
    list[WeeklyEvent],
    list[WeeklyEvent],
    list[SessionActivityEvent],
    dict[str, int],
    list[dict[str, Any]],
]:
    events: list[WeeklyEvent] = []
    quota_observations: list[WeeklyEvent] = []
    session_activity_events: list[SessionActivityEvent] = []
    file_audits: list[dict[str, Any]] = []
    stats = Counter()
    for path in _rollout_files(sessions_root):
        stats["files_scanned"] += 1
        physical_session_id = ""
        current_model = "unknown"
        physical_provider = ""
        file_started_at: datetime | None = None
        forked_from_id = ""
        replay_import_detected = False
        replay_transitioned = False
        audit: dict[str, Any] = {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "record_count": 0,
            "invalid_json_lines": 0,
            "token_count_records": 0,
            "replay_records_excluded": 0,
            "replay_token_counts_excluded": 0,
            "quota_observations": 0,
            "active_events": 0,
            "embedded_session_ids": [],
        }
        for line_number, line in enumerate(
            path.open("r", encoding="utf-8", errors="replace"),
            start=1,
        ):
            obj = _read_json_line(line)
            if not obj:
                stats["invalid_json_lines"] += 1
                audit["invalid_json_lines"] += 1
                continue
            audit["record_count"] += 1
            timestamp = _parse_timestamp(obj.get("timestamp"))
            if timestamp is not None:
                if not audit.get("first_record_at"):
                    audit["first_record_at"] = _iso_utc(timestamp)
                audit["last_record_at"] = _iso_utc(timestamp)

            if obj.get("type") == "session_meta":
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                session_id = str(payload.get("id") or "").strip()
                if not physical_session_id:
                    physical_session_id = session_id
                    physical_provider = str(payload.get("model_provider") or "").strip()
                    forked_from_id = str(payload.get("forked_from_id") or "").strip()
                    file_started_at = timestamp
                    audit["physical_session_id"] = physical_session_id
                    audit["forked_from_id"] = forked_from_id
                elif session_id and session_id != physical_session_id:
                    replay_import_detected = True
                    embedded = audit["embedded_session_ids"]
                    if session_id not in embedded:
                        embedded.append(session_id)
                continue

            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            if obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                audit["token_count_records"] += 1

            is_replayed_fork_record = bool(
                forked_from_id
                and replay_import_detected
                and file_started_at is not None
                and timestamp is not None
                and (timestamp - file_started_at).total_seconds()
                <= FORK_REPLAY_BURST_SECONDS
            )
            if is_replayed_fork_record:
                stats["fork_replay_records_excluded"] += 1
                audit["replay_records_excluded"] += 1
                if obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                    stats["fork_replay_token_counts_excluded"] += 1
                    audit["replay_token_counts_excluded"] += 1
                continue
            if forked_from_id and not replay_transitioned:
                replay_transitioned = True
                current_model = "unknown"

            if obj.get("type") == "turn_context":
                model = str(payload.get("model") or "").strip()
                if model:
                    current_model = model
                continue

            user_activity = _session_activity_from_user_message(
                obj,
                session_id=physical_session_id,
                since_utc=since_utc,
                until_utc=until_utc,
            )
            if user_activity is not None:
                session_activity_events.append(user_activity)
                stats["user_messages_accepted"] += 1

            observation = _quota_observation_from_token_count(
                obj,
                session_id=physical_session_id,
                model=current_model,
                model_provider=physical_provider,
                source_path=str(path),
                source_line=line_number,
                plan_type_filter=plan_type_filter,
                since_utc=since_utc,
                until_utc=until_utc,
            )
            if observation is not None:
                quota_observations.append(observation)
                audit["quota_observations"] += 1
                session_activity_events.append(SessionActivityEvent(
                    timestamp=observation.timestamp,
                    session_id=observation.session_id,
                    kind="internal_round",
                ))
                stats[f"quota_weekly_limit_source_{observation.limit_source}"] += 1

            event, reason = _event_from_token_count(
                obj,
                session_id=physical_session_id,
                model=current_model,
                model_provider=physical_provider,
                source_path=str(path),
                source_line=line_number,
                plan_type_filter=plan_type_filter,
                since_utc=since_utc,
                until_utc=until_utc,
                max_reset_lag_seconds=max_reset_lag_seconds,
            )
            stats[reason] += 1
            if event is not None:
                events.append(event)
                audit["active_events"] += 1
                stats[f"event_weekly_limit_source_{event.limit_source}"] += 1
        file_audits.append(audit)
    stats["events_accepted"] = len(events)
    stats["quota_observations"] = len(quota_observations)
    stats["session_activity_events"] = len(session_activity_events)
    return (
        events,
        quota_observations,
        session_activity_events,
        dict(stats),
        file_audits,
    )


def _session_activity_from_user_message(
    obj: dict[str, Any],
    *,
    session_id: str,
    since_utc: datetime,
    until_utc: datetime,
) -> SessionActivityEvent | None:
    timestamp = _parse_timestamp(obj.get("timestamp"))
    if timestamp is None or timestamp < since_utc or timestamp >= until_utc:
        return None
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    if obj.get("type") != "event_msg" or payload.get("type") != "user_message":
        return None
    return SessionActivityEvent(timestamp=timestamp, session_id=session_id, kind="user_input")


def load_daily_limit_snapshots(
    snapshot_root: Path,
    *,
    account_id: str,
    since_utc: datetime,
    until_utc: datetime,
    timezone_name: str,
    day_start_hour: int,
) -> tuple[dict[datetime, DailyLimitSnapshot], dict[str, Any]]:
    """Load the authoritative 05:00 limit observations for one account."""
    snapshots: dict[datetime, DailyLimitSnapshot] = {}
    audit: dict[str, Any] = {
        "root": str(snapshot_root),
        "account_id": account_id,
        "files_scanned": 0,
        "snapshots_loaded": 0,
        "snapshots_rejected": 0,
        "warnings": [],
        "sources": [],
    }
    if not snapshot_root.is_dir():
        audit["warnings"].append("snapshot_root_missing")
        return snapshots, audit

    tz = ZoneInfo(timezone_name)
    for path in sorted(snapshot_root.glob("????-??-??.json")):
        audit["files_scanned"] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(f"invalid_json:{path.name}")
            continue
        if not schema_matches(payload.get("schema"), "tfi.codex-daily-limit-snapshot.v2"):
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(f"unsupported_snapshot_schema:{path.name}")
            continue
        try:
            snapshot_day = date.fromisoformat(str(payload.get("snapshot_date") or path.stem))
        except Exception:
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(f"invalid_snapshot_date:{path.name}")
            continue
        boundary = datetime(
            snapshot_day.year,
            snapshot_day.month,
            snapshot_day.day,
            max(0, min(23, int(day_start_hour))),
            tzinfo=tz,
        ).astimezone(timezone.utc)
        if boundary < since_utc - timedelta(days=1) or boundary >= until_utc:
            continue

        accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
        account = next(
            (
                item for item in accounts
                if isinstance(item, dict)
                and str(item.get("account_id") or "") == account_id
            ),
            None,
        )
        if account is None:
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(f"account_missing:{path.name}")
            continue
        status = str(account.get("status") or "")
        stale = bool(account.get("stale"))
        if status != "ok" or stale:
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(f"unhealthy_snapshot:{path.name}")
            continue

        rate_limits = account.get("rate_limits") if isinstance(account.get("rate_limits"), list) else []
        weekly = next(
            (
                window for window in rate_limits
                if isinstance(window, dict)
                and _nonnegative_int(window.get("window_duration_mins")) == WEEKLY_WINDOW_MINUTES
            ),
            {},
        )
        remaining_percent = _float_or_none(weekly.get("remaining_percent"))
        used_percent = (
            round(100.0 - remaining_percent, 3)
            if remaining_percent is not None
            else None
        )
        reset_at = _nonnegative_int(weekly.get("resets_at"))
        observed_at = (
            _parse_timestamp(weekly.get("observed_at"))
            or _parse_timestamp(account.get("updated_at"))
            or _parse_timestamp(payload.get("observed_at"))
        )
        if (
            used_percent is None
            or not reset_at
            or observed_at is None
            or bool(weekly.get("stale"))
        ):
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(f"invalid_codex_limit:{path.name}")
            continue
        boundary_lag_minutes = abs(
            (observed_at - boundary).total_seconds() / 60
        )
        if boundary_lag_minutes > DAILY_SNAPSHOT_MAX_BOUNDARY_LAG_MINUTES:
            audit["snapshots_rejected"] += 1
            audit["warnings"].append(
                f"snapshot_outside_05am_window:{path.name}:{boundary_lag_minutes:.1f}m"
            )
            continue

        item = DailyLimitSnapshot(
            boundary=boundary,
            observed_at=observed_at,
            account_id=account_id,
            status=status,
            stale=stale,
            used_percent=used_percent,
            reset_at=reset_at,
            source_path=str(path),
        )
        current = snapshots.get(boundary)
        if current is None or item.observed_at > current.observed_at:
            snapshots[boundary] = item
        audit["sources"].append({
            "snapshot_date": snapshot_day.isoformat(),
            "boundary_at": _iso_utc(boundary),
            "observed_at": _iso_utc(observed_at),
            "used_percent": used_percent,
            "reset_at": reset_at,
            "path": str(path),
        })

    audit["snapshots_loaded"] = len(snapshots)
    return snapshots, audit


def _quota_observation_from_token_count(
    obj: dict[str, Any],
    *,
    session_id: str,
    model: str,
    model_provider: str,
    source_path: str,
    source_line: int,
    plan_type_filter: str,
    since_utc: datetime,
    until_utc: datetime,
) -> WeeklyEvent | None:
    timestamp = _parse_timestamp(obj.get("timestamp"))
    if timestamp is None or timestamp < since_utc or timestamp >= until_utc:
        return None
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    if obj.get("type") != "event_msg" or payload.get("type") != "token_count":
        return None
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    usage = _clean_usage(info.get("last_token_usage"))
    if not usage:
        return None
    rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    weekly_limit = _weekly_limit_from_rate_limits(rate_limits)
    if weekly_limit is None:
        return None
    limit_source, weekly = weekly_limit
    plan_type = str(rate_limits.get("plan_type") or "")
    if plan_type_filter and plan_type != plan_type_filter:
        return None
    return WeeklyEvent(
        timestamp=timestamp,
        session_id=session_id,
        model=model,
        model_provider=model_provider,
        plan_type=plan_type,
        limit_source=limit_source,
        reset_at=_nonnegative_int(weekly.get("resets_at")),
        used_percent=_float_or_none(weekly.get("used_percent")),
        usage=usage,
        source_path=source_path,
        source_line=source_line,
    )


def summarize_intervals(
    events: list[WeeklyEvent],
    quota_observations: list[WeeklyEvent],
    *,
    min_reset_events: int,
    include_empty_intervals: bool,
    include_initial_partial_interval: bool,
    generated_at: datetime,
    manual_depleted_resets: set[int],
    obsolete_resets: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_reset: dict[int, ResetBucket] = defaultdict(lambda: ResetBucket(reset_at=0))
    for event in events:
        bucket = by_reset[event.reset_at]
        bucket.reset_at = event.reset_at
        bucket.events.append(event)

    selected = [
        bucket
        for bucket in by_reset.values()
        if bucket.observed_count >= min_reset_events
    ]
    selected.sort(key=lambda bucket: bucket.reset_at)
    selected = _collapse_same_day_reset_buckets(selected, manual_depleted_resets)
    selected = [bucket for bucket in selected if bucket.reset_at not in obsolete_resets]
    selected_resets = {bucket.reset_at for bucket in selected}

    reset_rows = []
    for bucket in selected:
        limit_sources = Counter(event.limit_source for event in bucket.events)
        reset_rows.append({
            "reset_at": _iso_utc(datetime.fromtimestamp(bucket.reset_at, timezone.utc)),
            "reset_at_epoch": bucket.reset_at,
            "observed_count": bucket.observed_count,
            "first_event_at": _iso_utc(bucket.first_event),
            "last_event_at": _iso_utc(bucket.last_event),
            "max_used_percent": bucket.max_used_percent,
            "last_used_percent": bucket.last_used_percent,
            "last_observed_remaining_percent": _remaining(bucket.last_used_percent),
            "max_observed_remaining_percent": _remaining(bucket.max_used_percent),
            "weekly_limit_sources": dict(limit_sources.most_common()),
        })

    interval_rows: list[dict[str, Any]] = []
    def append_interval_row(
        *,
        start_dt: datetime,
        end_dt: datetime,
        usage_events: list[WeeklyEvent],
        wall_scoped: list[WeeklyEvent],
        scoped: list[WeeklyEvent],
        usage_event_source: str,
        quota_status: str | None = None,
        reported_used: float | None = None,
    ) -> None:
        usage: dict[str, int] = {}
        usage_by_model: dict[str, dict[str, Any]] = {}
        sessions = set()
        models = Counter()
        plans = Counter()
        limit_sources = Counter()
        for event in usage_events:
            _add_usage(usage, event.usage)
            model_key = event.model or "unknown"
            model_item = usage_by_model.setdefault(model_key, {
                "model": model_key,
                "event_count": 0,
                "session_ids": set(),
                "usage": {},
            })
            model_item["event_count"] += 1
            if event.session_id:
                model_item["session_ids"].add(event.session_id)
            _add_usage(model_item["usage"], event.usage)
            if event.session_id:
                sessions.add(event.session_id)
            models[event.model] += 1
            plans[event.plan_type] += 1
            limit_sources[event.limit_source] += 1
        last_event = max(usage_events, key=lambda event: event.timestamp, default=None)
        max_used = max((event.used_percent for event in usage_events if event.used_percent is not None), default=None)
        last_used = last_event.used_percent if last_event is not None else None
        wall_peak_used = max((event.used_percent for event in wall_scoped if event.used_percent is not None), default=None)
        if quota_status is None:
            if end_dt > generated_at:
                quota_status = "in_progress"
                reported_used = last_used
            else:
                quota_status = "closed"
                reported_used = max_used
        cost = _estimate_api_cost(usage_by_model)
        interval_rows.append({
            "interval_id": f"W{len(interval_rows) + 1:02d}",
            "interval_start_reset_at": _iso_utc(start_dt),
            "interval_end_reset_at": _iso_utc(end_dt),
            "interval_days": round((end_dt - start_dt).total_seconds() / 86400, 3),
            "event_count": len(usage_events),
            "session_count": len(sessions),
            "usage_event_source": usage_event_source,
            "ending_reset_bucket_event_count": len(scoped),
            "usage": {key: usage[key] for key in TOKEN_KEYS if usage.get(key)},
            "max_used_percent_before_reset": max_used,
            "last_used_percent_before_reset": last_used,
            "last_observed_remaining_percent_before_reset": _remaining(last_used),
            "max_observed_remaining_percent_before_reset": _remaining(max_used),
            "wall_interval_peak_used_percent": wall_peak_used,
            "wall_interval_min_observed_remaining_percent": _remaining(wall_peak_used),
            "wall_interval_quota_observation_count": len(wall_scoped),
            "reported_quota_status": quota_status,
            "reported_used_percent": reported_used,
            "reported_remaining_percent": _remaining(reported_used),
            "usage_by_model": _finalize_usage_by_model(usage_by_model),
            "estimated_api_cost_usd": cost,
            "estimated_full_usage_cost_usd": _estimate_full_usage_cost(cost.get("total_cost_usd"), reported_used),
            "last_event_at": _iso_utc(last_event.timestamp if last_event else None),
            "models": dict(models.most_common()),
            "plan_types": dict(plans.most_common()),
            "weekly_limit_sources": dict(limit_sources.most_common()),
        })

    if include_initial_partial_interval and selected:
        first_end_dt = datetime.fromtimestamp(selected[0].reset_at, timezone.utc)
        initial_observations = [
            event for event in quota_observations
            if event.timestamp < first_end_dt
        ]
        if initial_observations:
            initial_start_dt = min(event.timestamp for event in initial_observations)
            wall_scoped = [
                event for event in initial_observations
                if initial_start_dt <= event.timestamp < first_end_dt
            ]
            if wall_scoped or include_empty_intervals:
                append_interval_row(
                    start_dt=initial_start_dt,
                    end_dt=first_end_dt,
                    usage_events=wall_scoped,
                    wall_scoped=wall_scoped,
                    scoped=[],
                    usage_event_source="initial_wall_interval",
                    quota_status="initial_partial",
                    reported_used=max((event.used_percent for event in wall_scoped if event.used_percent is not None), default=None),
                )

    for index in range(1, len(selected)):
        start = selected[index - 1]
        end = selected[index]
        start_dt = datetime.fromtimestamp(start.reset_at, timezone.utc)
        end_dt = datetime.fromtimestamp(end.reset_at, timezone.utc)
        scoped = [
            event for event in end.events
            if start_dt <= event.timestamp < end_dt and event.reset_at in selected_resets
        ]
        wall_scoped = [
            event for event in quota_observations
            if start_dt <= event.timestamp < end_dt
        ]
        is_manual_depleted = end.reset_at in manual_depleted_resets
        if is_manual_depleted:
            if not wall_scoped and not include_empty_intervals:
                continue
            usage_events = wall_scoped
            usage_event_source = "wall_interval_manual_depleted"
            quota_status = "manual_depleted"
            reported_used = 100.0
        elif start.reset_at in manual_depleted_resets:
            if not wall_scoped and not include_empty_intervals:
                continue
            usage_events = wall_scoped
            usage_event_source = "wall_interval_post_manual_reset_transition"
            quota_status = "post_manual_reset_transition"
            reported_used = None
        else:
            if not scoped and not include_empty_intervals:
                continue
            usage_events = scoped
            usage_event_source = "ending_reset_bucket"
            quota_status = None
            reported_used = None
        append_interval_row(
            start_dt=start_dt,
            end_dt=end_dt,
            usage_events=usage_events,
            wall_scoped=wall_scoped,
            scoped=scoped,
            usage_event_source=usage_event_source,
            quota_status=quota_status,
            reported_used=reported_used,
        )
    return interval_rows, reset_rows


def summarize_reset_observations(quota_observations: list[WeeklyEvent]) -> list[dict[str, Any]]:
    by_reset: dict[int, list[WeeklyEvent]] = defaultdict(list)
    for event in quota_observations:
        if event.reset_at:
            by_reset[event.reset_at].append(event)

    rows = []
    for reset_at, items in sorted(by_reset.items()):
        items.sort(key=lambda event: event.timestamp)
        used_values = [event.used_percent for event in items if event.used_percent is not None]
        sessions = {event.session_id for event in items if event.session_id}
        limit_sources = Counter(event.limit_source for event in items)
        stale_count = sum(
            1
            for event in items
            if datetime.fromtimestamp(reset_at, timezone.utc) < event.timestamp - timedelta(seconds=60)
        )
        rows.append({
            "reset_at": _iso_utc(datetime.fromtimestamp(reset_at, timezone.utc)),
            "reset_at_epoch": reset_at,
            "observed_count": len(items),
            "session_count": len(sessions),
            "first_event_at": _iso_utc(items[0].timestamp),
            "last_event_at": _iso_utc(items[-1].timestamp),
            "min_used_percent": min(used_values) if used_values else None,
            "max_used_percent": max(used_values) if used_values else None,
            "last_used_percent": items[-1].used_percent,
            "weekly_limit_sources": dict(limit_sources.most_common()),
            "stale_observation_count": stale_count,
        })
    return rows


def infer_reset_events(
    events: list[WeeklyEvent],
    *,
    min_reset_events: int,
    manual_depleted_resets: set[int],
    auto_before_min_used: float,
    auto_after_max_used: float,
    auto_min_drop: float,
    manual_merge_window_seconds: int,
    timezone_name: str,
    same_reset_drop_min_events: int,
    same_reset_drop_min_sessions: int,
    min_inferred_reset_spacing_hours: int,
) -> list[dict[str, Any]]:
    by_reset: dict[int, ResetBucket] = defaultdict(lambda: ResetBucket(reset_at=0))
    for event in events:
        by_reset[event.reset_at].reset_at = event.reset_at
        by_reset[event.reset_at].events.append(event)

    durable = {
        reset_at: bucket
        for reset_at, bucket in by_reset.items()
        if bucket.observed_count >= min_reset_events
    }
    chronological = sorted(
        [event for event in events if event.used_percent is not None],
        key=lambda event: event.timestamp,
    )
    first_event_at = min((event.timestamp for event in events), default=None)

    inferred: list[InferredReset] = []
    for reset_at, bucket in sorted(durable.items()):
        bucket_events = sorted(bucket.events, key=lambda event: event.timestamp)
        low_events = [
            event
            for event in bucket_events
            if event.used_percent is not None and event.used_percent <= auto_after_max_used
        ]
        after = low_events[0] if low_events else bucket_events[0]
        before = _previous_different_reset_event(chronological, after)
        before_used = before.used_percent if before is not None else None
        after_used = after.used_percent
        from_reset_at = before.reset_at if before is not None else None
        event_at = _reset_event_hour(reset_at, after.timestamp, timezone_name)
        if first_event_at is not None and event_at < first_event_at:
            event_at = _floor_to_hour(first_event_at)
        if (
            before is not None
            and before_used is not None
            and after_used is not None
            and after.reset_at != before.reset_at
            and before_used >= auto_before_min_used
            and after_used <= auto_after_max_used
            and before_used - after_used >= auto_min_drop
        ):
            kind = "automatic_reset_from_usage_drop"
            evidence = f"reset_at-derived hour; usage drop observed at {_iso_utc(after.timestamp)}"
        elif after_used is not None and after_used <= auto_after_max_used:
            kind = "automatic_reset_from_low_usage_start"
            evidence = f"reset_at-derived hour; low-usage observation first seen at {_iso_utc(after.timestamp)}"
        else:
            kind = "reset_at_first_seen_without_low_usage"
            evidence = f"reset_at-derived hour; durable reset_at first seen at {_iso_utc(after.timestamp)}"
        inferred.append(InferredReset(
            event_at=event_at,
            reset_kind=kind,
            to_reset_at=reset_at,
            from_reset_at=from_reset_at,
            before_used_percent=before_used,
            after_used_percent=after_used,
            observed_count=bucket.observed_count,
            session_count=len({event.session_id for event in bucket.events if event.session_id}),
            evidence=evidence,
        ))

    inferred.extend(_same_reset_usage_drop_events(
        chronological,
        durable_resets=set(durable),
        auto_before_min_used=auto_before_min_used,
        auto_after_max_used=auto_after_max_used,
        auto_min_drop=auto_min_drop,
        timezone_name=timezone_name,
        min_events=same_reset_drop_min_events,
        min_sessions=same_reset_drop_min_sessions,
    ))

    for manual_reset_at in sorted(manual_depleted_resets):
        manual_dt = _floor_to_hour(datetime.fromtimestamp(manual_reset_at, timezone.utc))
        nearest = _nearest_inferred_after(inferred, manual_dt, manual_merge_window_seconds)
        if nearest is not None:
            nearest.event_at = manual_dt
            nearest.reset_kind = "manual_depleted_reset"
            nearest.before_used_percent = 100.0
            nearest.evidence = "explicit manual/depleted reset marker supplied by operator"
        else:
            after_bucket = _first_bucket_after(durable, manual_dt)
            inferred.append(InferredReset(
                event_at=manual_dt,
                reset_kind="manual_depleted_reset",
                to_reset_at=after_bucket.reset_at if after_bucket is not None else manual_reset_at,
                from_reset_at=None,
                before_used_percent=100.0,
                after_used_percent=after_bucket.last_used_percent if after_bucket is not None else None,
                observed_count=after_bucket.observed_count if after_bucket is not None else 0,
                session_count=len({event.session_id for event in after_bucket.events if event.session_id}) if after_bucket is not None else 0,
                evidence="explicit manual/depleted reset marker supplied by operator",
            ))

    local_tz = ZoneInfo(timezone_name)
    deduped: dict[str, InferredReset] = {}
    for item in sorted(inferred, key=lambda reset: (reset.event_at, reset.to_reset_at)):
        key = item.event_at.astimezone(local_tz).date().isoformat()
        current = deduped.get(key)
        if current is None or _reset_priority(item) > _reset_priority(current):
            deduped[key] = item

    result = _collapse_close_inferred_resets(
        sorted(deduped.values(), key=lambda reset: (reset.event_at, reset.to_reset_at)),
        min_spacing=timedelta(hours=max(0, min_inferred_reset_spacing_hours)),
    )
    return [
        {
            "event_at": _iso_utc(item.event_at),
            "reset_kind": item.reset_kind,
            "from_reset_at": _iso_utc(datetime.fromtimestamp(item.from_reset_at, timezone.utc)) if item.from_reset_at else "",
            "from_reset_at_epoch": item.from_reset_at,
            "to_reset_at": _iso_utc(datetime.fromtimestamp(item.to_reset_at, timezone.utc)),
            "to_reset_at_epoch": item.to_reset_at,
            "before_used_percent": item.before_used_percent,
            "after_used_percent": item.after_used_percent,
            "observed_count": item.observed_count,
            "session_count": item.session_count,
            "evidence": item.evidence,
        }
        for item in result
    ]


def summarize_intervals_from_inferred_resets(
    quota_observations: list[WeeklyEvent],
    inferred_reset_rows: list[dict[str, Any]],
    *,
    include_initial_partial_interval: bool,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    inferred = [
        row for row in inferred_reset_rows
        if _parse_timestamp(row.get("event_at")) is not None
    ]
    inferred.sort(key=lambda row: _parse_timestamp(row.get("event_at")) or datetime.min.replace(tzinfo=timezone.utc))
    if not inferred:
        return []

    interval_rows: list[dict[str, Any]] = []

    def append_event_interval(
        *,
        start_dt: datetime,
        end_dt: datetime,
        usage_events: list[WeeklyEvent],
        ending_reset: dict[str, Any] | None,
        quota_status: str,
        reported_used: float | None,
        usage_event_source: str,
    ) -> None:
        usage: dict[str, int] = {}
        usage_by_model: dict[str, dict[str, Any]] = {}
        sessions = set()
        models = Counter()
        plans = Counter()
        limit_sources = Counter()
        for event in usage_events:
            _add_usage(usage, event.usage)
            model_key = event.model or "unknown"
            model_item = usage_by_model.setdefault(model_key, {
                "model": model_key,
                "event_count": 0,
                "session_ids": set(),
                "usage": {},
            })
            model_item["event_count"] += 1
            if event.session_id:
                model_item["session_ids"].add(event.session_id)
                sessions.add(event.session_id)
            _add_usage(model_item["usage"], event.usage)
            models[event.model] += 1
            plans[event.plan_type] += 1
            limit_sources[event.limit_source] += 1
        last_event = max(usage_events, key=lambda event: event.timestamp, default=None)
        max_used = max((event.used_percent for event in usage_events if event.used_percent is not None), default=None)
        last_used = last_event.used_percent if last_event is not None else None
        cost = _estimate_api_cost(usage_by_model)
        interval_rows.append({
            "interval_id": f"W{len(interval_rows) + 1:02d}",
            "interval_start_reset_at": _iso_utc(start_dt),
            "interval_end_reset_at": _iso_utc(end_dt),
            "interval_days": round((end_dt - start_dt).total_seconds() / 86400, 3),
            "event_count": len(usage_events),
            "session_count": len(sessions),
            "usage_event_source": usage_event_source,
            "ending_reset_bucket_event_count": 0,
            "ending_reset_event": ending_reset or {},
            "usage": {key: usage[key] for key in TOKEN_KEYS if usage.get(key)},
            "max_used_percent_before_reset": max_used,
            "last_used_percent_before_reset": last_used,
            "last_observed_remaining_percent_before_reset": _remaining(last_used),
            "max_observed_remaining_percent_before_reset": _remaining(max_used),
            "wall_interval_peak_used_percent": max_used,
            "wall_interval_min_observed_remaining_percent": _remaining(max_used),
            "wall_interval_quota_observation_count": len(usage_events),
            "reported_quota_status": quota_status,
            "reported_used_percent": reported_used,
            "reported_remaining_percent": _remaining(reported_used),
            "usage_by_model": _finalize_usage_by_model(usage_by_model),
            "estimated_api_cost_usd": cost,
            "estimated_full_usage_cost_usd": _estimate_full_usage_cost(cost.get("total_cost_usd"), reported_used),
            "last_event_at": _iso_utc(last_event.timestamp if last_event else None),
            "models": dict(models.most_common()),
            "plan_types": dict(plans.most_common()),
            "weekly_limit_sources": dict(limit_sources.most_common()),
        })

    first_start = _parse_timestamp(inferred[0].get("event_at"))
    if include_initial_partial_interval and first_start is not None:
        earlier = [event for event in quota_observations if event.timestamp < first_start]
        if earlier:
            start_dt = min(event.timestamp for event in earlier)
            append_event_interval(
                start_dt=start_dt,
                end_dt=first_start,
                usage_events=[event for event in earlier if start_dt <= event.timestamp < first_start],
                ending_reset=inferred[0],
                quota_status="initial_partial",
                reported_used=_float_or_none(inferred[0].get("before_used_percent")) or max((event.used_percent for event in earlier if event.used_percent is not None), default=None),
                usage_event_source="wall_interval_before_first_inferred_reset",
            )

    for index in range(1, len(inferred)):
        start_dt = _parse_timestamp(inferred[index - 1].get("event_at"))
        end_dt = _parse_timestamp(inferred[index].get("event_at"))
        if start_dt is None or end_dt is None or end_dt <= start_dt:
            continue
        usage_events = [event for event in quota_observations if start_dt <= event.timestamp < end_dt]
        ending_reset = inferred[index]
        end_kind = str(ending_reset.get("reset_kind") or "")
        if end_kind == "manual_depleted_reset":
            status = "closed_manual_reset"
            reported_used = 100.0
        elif end_kind in {"automatic_reset_from_usage_drop", "automatic_reset_from_usage_drop_without_reset_at_change"}:
            status = "closed_auto_reset"
            reported_used = _float_or_none(ending_reset.get("before_used_percent"))
            if reported_used is None:
                reported_used = max((event.used_percent for event in usage_events if event.used_percent is not None), default=None)
        elif end_kind == "automatic_reset_from_low_usage_start":
            status = "closed_auto_reset"
            reported_used = max((event.used_percent for event in usage_events if event.used_percent is not None), default=None)
        else:
            status = "closed_reset_at_change"
            reported_used = max((event.used_percent for event in usage_events if event.used_percent is not None), default=None)
        append_event_interval(
            start_dt=start_dt,
            end_dt=end_dt,
            usage_events=usage_events,
            ending_reset=ending_reset,
            quota_status=status,
            reported_used=reported_used,
            usage_event_source="wall_interval_between_inferred_resets",
        )

    last_reset = inferred[-1]
    last_start = _parse_timestamp(last_reset.get("event_at"))
    if last_start is not None:
        to_reset_at = _parse_timestamp(last_reset.get("to_reset_at"))
        end_dt = _floor_to_hour(to_reset_at) if to_reset_at is not None and to_reset_at > generated_at else _floor_to_hour(generated_at)
        usage_events = [event for event in quota_observations if last_start <= event.timestamp < end_dt]
        latest_same_bucket = _latest_event_for_reset(quota_observations, _nonnegative_int(last_reset.get("to_reset_at_epoch")))
        reported_used = latest_same_bucket.used_percent if latest_same_bucket is not None else None
        append_event_interval(
            start_dt=last_start,
            end_dt=end_dt,
            usage_events=usage_events,
            ending_reset=None,
            quota_status="in_progress",
            reported_used=reported_used,
            usage_event_source="wall_interval_after_latest_inferred_reset",
        )
    return interval_rows


def summarize_daily_intervals(
    events: list[WeeklyEvent],
    quota_observations: list[WeeklyEvent],
    inferred_reset_rows: list[dict[str, Any]],
    *,
    generated_at: datetime,
    range_until_utc: datetime,
    timezone_name: str,
    day_start_hour: int,
    daily_snapshots: dict[datetime, DailyLimitSnapshot] | None = None,
) -> list[dict[str, Any]]:
    snapshots = daily_snapshots or {}
    if not quota_observations and not snapshots:
        return []
    tz = ZoneInfo(timezone_name)
    source_times = [event.timestamp for event in quota_observations]
    source_times.extend(snapshots)
    start_dt = _daily_boundary_for(min(source_times), tz, day_start_hour)
    end_limit = min(generated_at, range_until_utc)
    end_dt = _next_daily_boundary(end_limit, tz, day_start_hour)
    rows = []
    current = start_dt
    while current < end_dt:
        following = current + timedelta(days=1)
        usage_events = [event for event in quota_observations if current <= event.timestamp < following]
        alignment_events = [event for event in events if current <= event.timestamp < following]
        next_alignment_events = [event for event in events if following <= event.timestamp < following + timedelta(days=1)]
        reset_rows = [
            row for row in inferred_reset_rows
            if current <= (_parse_timestamp(row.get("event_at")) or datetime.min.replace(tzinfo=timezone.utc)) < following
        ]
        rows.append(_daily_interval_row(
            row_id=f"D{len(rows) + 1:02d}",
            start_dt=current,
            end_dt=following,
            usage_events=usage_events,
            alignment_events=alignment_events,
            next_alignment_events=next_alignment_events,
            reset_rows=reset_rows,
            generated_at=generated_at,
            start_snapshot=snapshots.get(current),
            next_snapshot=snapshots.get(following),
        ))
        current = following
    return rows


def annotate_daily_weekly_equivalent_estimates(
    rows: list[dict[str, Any]],
    *,
    max_window_days: int = DAILY_ESTIMATE_MAX_WINDOW_DAYS,
    min_window_days: int = DAILY_ESTIMATE_MIN_WINDOW_DAYS,
    min_usage_percent: float = DAILY_ESTIMATE_MIN_USAGE_PERCENT,
    max_alignment_lag_minutes: float = DAILY_ESTIMATE_MAX_ALIGNMENT_LAG_MINUTES,
) -> list[dict[str, Any]]:
    """Replace noisy single-day extrapolation with a short rolling estimate."""
    rolling: list[dict[str, Any]] = []
    previous_end: datetime | None = None
    previous_had_reset = False

    for row in rows:
        start_at = _parse_timestamp(row.get("day_start_at"))
        end_at = _parse_timestamp(row.get("day_end_at"))
        cost = row.get("estimated_api_cost_usd") if isinstance(row.get("estimated_api_cost_usd"), dict) else {}
        cost_usd = _float_or_none(cost.get("total_cost_usd"))
        usage_percent = _float_or_none(row.get("usage_change_used_percent_24h"))
        reset_count = _nonnegative_int(row.get("usage_reset_count_24h"))
        has_inputs = (
            start_at is not None
            and end_at is not None
            and cost_usd is not None
            and cost_usd >= 0
            and usage_percent is not None
            and usage_percent > 0
        )

        is_consecutive = previous_end is not None and start_at == previous_end
        if not has_inputs or not is_consecutive or previous_had_reset or reset_count:
            rolling = []

        if has_inputs:
            rolling.append(row)
            rolling = rolling[-max(1, int(max_window_days)):]

            window_cost = 0.0
            window_usage = 0.0
            window_reset_count = 0
            alignment_lags: list[float] = []
            missing_alignment = False
            for item in rolling:
                item_cost = item.get("estimated_api_cost_usd") if isinstance(item.get("estimated_api_cost_usd"), dict) else {}
                window_cost += _float_or_none(item_cost.get("total_cost_usd")) or 0.0
                window_usage += _float_or_none(item.get("usage_change_used_percent_24h")) or 0.0
                window_reset_count += _nonnegative_int(item.get("usage_reset_count_24h"))
                for alignment_key in ("usage_alignment", "next_usage_alignment"):
                    alignment = item.get(alignment_key)
                    lag = _float_or_none(alignment.get("lag_minutes")) if isinstance(alignment, dict) else None
                    if lag is None:
                        missing_alignment = True
                    else:
                        alignment_lags.append(lag)

            reasons = []
            if len(rolling) < max(1, int(min_window_days)):
                reasons.append("fewer_than_minimum_consecutive_days")
            if window_usage < float(min_usage_percent):
                reasons.append("cumulative_usage_change_below_minimum")
            if missing_alignment:
                reasons.append("missing_boundary_observation")
            elif any(lag > float(max_alignment_lag_minutes) for lag in alignment_lags):
                reasons.append("boundary_observation_lag_above_maximum")
            if window_reset_count:
                reasons.append("reset_inside_window")

            row["estimated_100_percent_weekly_cost_usd"] = _estimate_full_usage_cost(window_cost, window_usage)
            row["weekly_equivalent_estimate"] = {
                "method": "rolling_consecutive_daily_cost_divided_by_limit_change",
                "confidence": "high" if not reasons else "low",
                "low_confidence_reasons": reasons,
                "window_days": len(rolling),
                "window_start_at": str(rolling[0].get("day_start_at") or ""),
                "window_end_at": str(rolling[-1].get("day_end_at") or ""),
                "window_cost_usd": round(window_cost, 6),
                "window_usage_change_percent": round(window_usage, 3),
                "minimum_window_days": max(1, int(min_window_days)),
                "minimum_usage_change_percent": float(min_usage_percent),
                "maximum_alignment_lag_minutes": float(max_alignment_lag_minutes),
                "requires_no_reset": True,
            }
        else:
            row["estimated_100_percent_weekly_cost_usd"] = None
            row["weekly_equivalent_estimate"] = {
                "method": "rolling_consecutive_daily_cost_divided_by_limit_change",
                "confidence": "unavailable",
                "low_confidence_reasons": ["missing_or_nonpositive_daily_limit_change"],
                "window_days": 0,
                "window_start_at": "",
                "window_end_at": "",
                "window_cost_usd": None,
                "window_usage_change_percent": None,
                "minimum_window_days": max(1, int(min_window_days)),
                "minimum_usage_change_percent": float(min_usage_percent),
                "maximum_alignment_lag_minutes": float(max_alignment_lag_minutes),
                "requires_no_reset": True,
            }

        previous_end = end_at
        previous_had_reset = bool(reset_count)

    return rows


def _daily_interval_row(
    *,
    row_id: str,
    start_dt: datetime,
    end_dt: datetime,
    usage_events: list[WeeklyEvent],
    alignment_events: list[WeeklyEvent],
    next_alignment_events: list[WeeklyEvent],
    reset_rows: list[dict[str, Any]],
    generated_at: datetime,
    start_snapshot: DailyLimitSnapshot | None = None,
    next_snapshot: DailyLimitSnapshot | None = None,
) -> dict[str, Any]:
    usage: dict[str, int] = {}
    usage_by_model: dict[str, dict[str, Any]] = {}
    sessions = set()
    models = Counter()
    plans = Counter()
    limit_sources = Counter()
    for event in usage_events:
        _add_usage(usage, event.usage)
        model_key = event.model or "unknown"
        model_item = usage_by_model.setdefault(model_key, {
            "model": model_key,
            "event_count": 0,
            "session_ids": set(),
            "usage": {},
        })
        model_item["event_count"] += 1
        if event.session_id:
            model_item["session_ids"].add(event.session_id)
            sessions.add(event.session_id)
        _add_usage(model_item["usage"], event.usage)
        models[event.model] += 1
        plans[event.plan_type] += 1
        limit_sources[event.limit_source] += 1

    first_event = min(usage_events, key=lambda event: event.timestamp, default=None)
    first_alignment_event = min(alignment_events, key=lambda event: event.timestamp, default=None)
    next_alignment_event = min(next_alignment_events, key=lambda event: event.timestamp, default=None)
    last_event = max(usage_events, key=lambda event: event.timestamp, default=None)
    if start_snapshot is not None:
        usage_alignment = _daily_snapshot_alignment(start_dt, start_snapshot)
    else:
        usage_alignment = _daily_usage_alignment(start_dt, first_alignment_event)
    if next_snapshot is not None:
        next_usage_alignment = _daily_snapshot_alignment(end_dt, next_snapshot)
    else:
        next_usage_alignment = _daily_usage_alignment(end_dt, next_alignment_event)
    if start_snapshot is not None and next_snapshot is not None:
        usage_change_used_percent, snapshot_reset_count, usage_change_status = (
            _daily_snapshot_used_percent_change(start_snapshot, next_snapshot)
        )
        usage_reset_count = snapshot_reset_count
        usage_change_source = "daily_limit_snapshots"
    else:
        usage_change_used_percent = _daily_used_percent_change(
            first_alignment_event,
            next_alignment_event,
            reset_rows,
        )
        usage_reset_count = len(reset_rows)
        usage_change_status = "ok" if usage_change_used_percent is not None else "unavailable"
        usage_change_source = "session_weekly_observations_fallback"
    cost = _estimate_api_cost(usage_by_model)
    return {
        "day_id": row_id,
        "day_start_at": _iso_utc(start_dt),
        "day_end_at": _iso_utc(end_dt),
        "interval_days": round((end_dt - start_dt).total_seconds() / 86400, 3),
        "status": "in_progress" if start_dt <= generated_at < end_dt else "closed",
        "event_count": len(usage_events),
        "session_count": len(sessions),
        "usage": {key: usage[key] for key in TOKEN_KEYS if usage.get(key)},
        "usage_by_model": _finalize_usage_by_model(usage_by_model),
        "estimated_api_cost_usd": cost,
        "first_event_at": _iso_utc(first_event.timestamp if first_event else None),
        "last_event_at": _iso_utc(last_event.timestamp if last_event else None),
        "models": dict(models.most_common()),
        "plan_types": dict(plans.most_common()),
        "weekly_limit_sources": dict(limit_sources.most_common()),
        "usage_alignment": usage_alignment,
        "next_usage_alignment": next_usage_alignment,
        "usage_reset_count_24h": usage_reset_count,
        "usage_change_used_percent_24h": usage_change_used_percent,
        "usage_change_source": usage_change_source,
        "usage_change_status": usage_change_status,
        "estimated_100_percent_weekly_cost_usd": _estimate_full_usage_cost(cost.get("total_cost_usd"), usage_change_used_percent),
    }


def summarize_daily_session_stats(
    activity_events: list[SessionActivityEvent],
    daily_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for daily_row in daily_rows:
        start_dt = _parse_timestamp(daily_row.get("day_start_at"))
        end_dt = _parse_timestamp(daily_row.get("day_end_at"))
        if start_dt is None or end_dt is None:
            continue
        items = [event for event in activity_events if start_dt <= event.timestamp < end_dt]
        user_input_count = sum(1 for event in items if event.kind == "user_input")
        internal_round_count = sum(1 for event in items if event.kind == "internal_round")
        session_count = len({event.session_id for event in items if event.session_id})
        cost = daily_row.get("estimated_api_cost_usd") if isinstance(daily_row.get("estimated_api_cost_usd"), dict) else {}
        daily_cost = _float_or_none(cost.get("total_cost_usd"))
        average_cost = round(daily_cost / user_input_count, 6) if daily_cost is not None and user_input_count else None
        rows.append({
            "day_start_at": _iso_utc(start_dt),
            "day_end_at": _iso_utc(end_dt),
            "session_count": session_count,
            "user_input_count": user_input_count,
            "internal_round_count": internal_round_count,
            "priced_api_cost_usd": daily_cost,
            "average_cost_per_user_input_usd": average_cost,
        })
    return rows


def _daily_snapshot_alignment(
    start_dt: datetime,
    snapshot: DailyLimitSnapshot,
) -> dict[str, Any]:
    return {
        "method": "daily_05am_limit_snapshot",
        "observed_at": _iso_utc(snapshot.observed_at),
        "lag_minutes": round(
            (snapshot.observed_at - start_dt).total_seconds() / 60,
            3,
        ),
        "used_percent": snapshot.used_percent,
        "remaining_percent": _remaining(snapshot.used_percent),
        "reset_at": _iso_utc(
            datetime.fromtimestamp(snapshot.reset_at, timezone.utc)
        ) if snapshot.reset_at else "",
        "reset_at_epoch": snapshot.reset_at or None,
        "weekly_limit_source": "codex_daily_limit_snapshot",
        "source_path": snapshot.source_path,
        "status": snapshot.status,
        "stale": snapshot.stale,
    }


def _daily_snapshot_used_percent_change(
    start_snapshot: DailyLimitSnapshot,
    next_snapshot: DailyLimitSnapshot,
) -> tuple[float | None, int, str]:
    start_used = _float_or_none(start_snapshot.used_percent)
    end_used = _float_or_none(next_snapshot.used_percent)
    if start_used is None or end_used is None:
        return None, 0, "missing_used_percent"
    if abs(start_snapshot.reset_at - next_snapshot.reset_at) > RESET_AT_EQUIVALENCE_SECONDS:
        return round(max(end_used, 0.0), 3), 1, "reset_at_changed"
    if end_used < start_used:
        return None, 0, "used_percent_decreased_without_reset_at_change"
    return round(end_used - start_used, 3), 0, "ok"


def _daily_usage_alignment(start_dt: datetime, first_event: WeeklyEvent | None) -> dict[str, Any]:
    if first_event is None:
        return {
            "method": "first_weekly_observation_after_05:00_local",
            "observed_at": "",
            "lag_minutes": None,
            "used_percent": None,
            "remaining_percent": None,
            "reset_at": "",
            "reset_at_epoch": None,
            "weekly_limit_source": "",
        }
    return {
        "method": "first_weekly_observation_after_05:00_local",
        "observed_at": _iso_utc(first_event.timestamp),
        "lag_minutes": round((first_event.timestamp - start_dt).total_seconds() / 60, 3),
        "used_percent": first_event.used_percent,
        "remaining_percent": _remaining(first_event.used_percent),
        "reset_at": _iso_utc(datetime.fromtimestamp(first_event.reset_at, timezone.utc)) if first_event.reset_at else "",
        "reset_at_epoch": first_event.reset_at or None,
        "weekly_limit_source": first_event.limit_source,
    }


def _daily_used_percent_change(
    start_event: WeeklyEvent | None,
    next_start_event: WeeklyEvent | None,
    reset_rows: list[dict[str, Any]],
) -> float | None:
    if start_event is None or next_start_event is None:
        return None
    start_used = _float_or_none(start_event.used_percent)
    end_used = _float_or_none(next_start_event.used_percent)
    if start_used is None or end_used is None:
        return None
    previous_used = start_used
    total = 0.0
    for row in sorted(reset_rows, key=lambda item: str(item.get("event_at") or "")):
        before_used = _float_or_none(row.get("before_used_percent"))
        after_used = _float_or_none(row.get("after_used_percent"))
        if before_used is not None and before_used > previous_used:
            total += before_used - previous_used
        previous_used = after_used if after_used is not None else 0.0
    if end_used >= previous_used:
        total += end_used - previous_used
    return round(max(total, 0.0), 3)


def _previous_different_reset_event(events: list[WeeklyEvent], event: WeeklyEvent) -> WeeklyEvent | None:
    for candidate in reversed(events):
        if candidate.timestamp >= event.timestamp:
            continue
        if candidate.reset_at != event.reset_at and candidate.used_percent is not None:
            return candidate
    return None


def _reset_event_hour(reset_at: int, observed_at: datetime, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    reset_date_anchor = (
        datetime.fromtimestamp(reset_at, timezone.utc)
        - timedelta(minutes=WEEKLY_WINDOW_MINUTES)
    ).astimezone(tz)
    return _floor_to_hour(reset_date_anchor.astimezone(timezone.utc))


def _floor_to_hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _daily_boundary_for(value: datetime, tz: ZoneInfo, day_start_hour: int) -> datetime:
    local = value.astimezone(tz)
    hour = max(0, min(23, day_start_hour))
    boundary = datetime(local.year, local.month, local.day, hour, tzinfo=tz).astimezone(timezone.utc)
    if value < boundary:
        boundary -= timedelta(days=1)
    return boundary


def _next_daily_boundary(value: datetime, tz: ZoneInfo, day_start_hour: int) -> datetime:
    boundary = _daily_boundary_for(value, tz, day_start_hour)
    if boundary <= value:
        boundary += timedelta(days=1)
    return boundary


def _same_reset_usage_drop_events(
    events: list[WeeklyEvent],
    *,
    durable_resets: set[int],
    auto_before_min_used: float,
    auto_after_max_used: float,
    auto_min_drop: float,
    timezone_name: str,
    min_events: int,
    min_sessions: int,
) -> list[InferredReset]:
    inferred: list[InferredReset] = []
    last: WeeklyEvent | None = None
    seen_hours: set[tuple[int, datetime]] = set()
    for event in events:
        if event.reset_at not in durable_resets or event.used_percent is None:
            last = event
            continue
        if (
            last is not None
            and last.reset_at == event.reset_at
            and last.used_percent is not None
            and last.used_percent >= auto_before_min_used
            and event.used_percent <= auto_after_max_used
            and last.used_percent - event.used_percent >= auto_min_drop
        ):
            event_hour = _floor_to_hour(event.timestamp)
            key = (event.reset_at, event_hour)
            if key not in seen_hours and _same_reset_drop_supported(
                events,
                reset_at=event.reset_at,
                event_hour=event_hour,
                after_max_used=auto_after_max_used,
                min_events=min_events,
                min_sessions=min_sessions,
            ):
                seen_hours.add(key)
                inferred.append(InferredReset(
                    event_at=event_hour,
                    reset_kind="automatic_reset_from_usage_drop_without_reset_at_change",
                    to_reset_at=event.reset_at,
                    from_reset_at=last.reset_at,
                    before_used_percent=last.used_percent,
                    after_used_percent=event.used_percent,
                    observed_count=0,
                    session_count=0,
                    evidence="used_percent dropped from high to low without a durable reset_at change; hour has multi-session low-usage support",
                ))
        last = event
    return inferred


def _same_reset_drop_supported(
    events: list[WeeklyEvent],
    *,
    reset_at: int,
    event_hour: datetime,
    after_max_used: float,
    min_events: int,
    min_sessions: int,
) -> bool:
    end = event_hour + timedelta(hours=1)
    matching = [
        event for event in events
        if event.reset_at == reset_at
        and event_hour <= event.timestamp < end
        and event.used_percent is not None
        and event.used_percent <= after_max_used
    ]
    sessions = {event.session_id for event in matching if event.session_id}
    return len(matching) >= min_events and len(sessions) >= min_sessions


def _nearest_inferred_after(items: list[InferredReset], event_at: datetime, window_seconds: int) -> InferredReset | None:
    candidates = [
        item for item in items
        if abs((item.event_at - event_at).total_seconds()) <= window_seconds
    ]
    if not candidates:
        candidates = [
            item for item in items
            if 0 <= (item.event_at - event_at).total_seconds() <= window_seconds
        ]
    return min(candidates, key=lambda item: abs((item.event_at - event_at).total_seconds()), default=None)


def _first_bucket_after(buckets: dict[int, ResetBucket], event_at: datetime) -> ResetBucket | None:
    future = [bucket for bucket in buckets.values() if bucket.first_event and bucket.first_event >= event_at]
    return min(future, key=lambda bucket: bucket.first_event or datetime.max.replace(tzinfo=timezone.utc), default=None)


def _reset_priority(item: InferredReset) -> tuple[int, int, int]:
    kind_score = {
        "manual_depleted_reset": 3,
        "automatic_reset_from_usage_drop": 2,
        "automatic_reset_from_usage_drop_without_reset_at_change": 2,
        "automatic_reset_from_low_usage_start": 1,
    }.get(item.reset_kind, 0)
    return (kind_score, item.observed_count, item.session_count, int(item.event_at.timestamp()))


def _collapse_close_inferred_resets(items: list[InferredReset], *, min_spacing: timedelta) -> list[InferredReset]:
    if not items or min_spacing <= timedelta(0):
        return items
    clusters: list[list[InferredReset]] = []
    current: list[InferredReset] = []
    for item in items:
        if not current:
            current = [item]
            continue
        if item.event_at - current[-1].event_at < min_spacing:
            current.append(item)
            continue
        clusters.append(current)
        current = [item]
    if current:
        clusters.append(current)
    return sorted(
        [max(cluster, key=_reset_priority) for cluster in clusters],
        key=lambda reset: (reset.event_at, reset.to_reset_at),
    )


def _latest_event_for_reset(events: list[WeeklyEvent], reset_at: int) -> WeeklyEvent | None:
    if not reset_at:
        return None
    matching = [event for event in events if event.reset_at == reset_at and event.used_percent is not None]
    return max(matching, key=lambda event: event.timestamp, default=None)


def _collapse_same_day_reset_buckets(buckets: list[ResetBucket], manual_depleted_resets: set[int]) -> list[ResetBucket]:
    by_day: dict[date, list[ResetBucket]] = defaultdict(list)
    for bucket in buckets:
        day = datetime.fromtimestamp(bucket.reset_at, timezone.utc).date()
        by_day[day].append(bucket)
    collapsed = []
    for day in sorted(by_day):
        items = by_day[day]
        if len(items) == 1:
            collapsed.append(items[0])
            continue
        collapsed.append(max(
            items,
            key=lambda bucket: (
                bucket.reset_at in manual_depleted_resets,
                bucket.observed_count,
                bucket.reset_at,
            ),
        ))
    return collapsed


def _pricing_model(model: str) -> str:
    return MODEL_PRICE_ALIASES.get(model, model)


def _finalize_usage_by_model(raw: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for model, item in sorted(raw.items()):
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        session_ids = item.get("session_ids") if isinstance(item.get("session_ids"), set) else set()
        rows.append({
            "model": model,
            "pricing_model": _pricing_model(model),
            "event_count": item.get("event_count") or 0,
            "session_count": len(session_ids),
            "usage": {key: usage[key] for key in TOKEN_KEYS if usage.get(key)},
        })
    return rows


def _estimate_api_cost(raw: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_model = []
    total = 0.0
    unpriced_models = []
    for model, item in sorted(raw.items()):
        pricing_model = _pricing_model(model)
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        price = API_PRICE_PER_1M.get(pricing_model)
        if not price:
            unpriced_models.append(model)
            by_model.append({
                "model": model,
                "pricing_model": pricing_model,
                "priced": False,
                "usage": {key: usage[key] for key in TOKEN_KEYS if usage.get(key)},
            })
            continue
        input_tokens = _nonnegative_int(usage.get("input_tokens"))
        cached_input_tokens = min(_nonnegative_int(usage.get("cached_input_tokens")), input_tokens)
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        output_tokens = _nonnegative_int(usage.get("output_tokens"))
        cost = (
            (uncached_input_tokens / 1_000_000) * price["input"]
            + (cached_input_tokens / 1_000_000) * price["cached_input"]
            + (output_tokens / 1_000_000) * price["output"]
        )
        total += cost
        by_model.append({
            "model": model,
            "pricing_model": pricing_model,
            "priced": True,
            "price_per_1m": price,
            "uncached_input_tokens": uncached_input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
        })
    return {
        "pricing_source": PRICING_SOURCE_URL,
        "pricing_basis": "OpenAI API Standard short-context prices per 1M tokens",
        "formula": "uncached_input_tokens*input_price + cached_input_tokens*cached_input_price + output_tokens*output_price",
        "total_cost_usd": round(total, 6),
        "by_model": by_model,
        "unpriced_models": unpriced_models,
    }


def _estimate_full_usage_cost(cost_usd: object, used_percent: object) -> float | None:
    try:
        cost = float(cost_usd)
        used = float(used_percent)
    except (TypeError, ValueError, OverflowError):
        return None
    if used <= 0:
        return None
    return round(cost * 100.0 / used, 6)


def _remaining(used_percent: float | None) -> float | None:
    if used_percent is None:
        return None
    remaining = 100.0 - used_percent
    return round(remaining if remaining > 0 else 0.0, 3)


def _iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local(value: str, timezone_name: str) -> str:
    if not value:
        return ""
    parsed = _parse_timestamp(value)
    if parsed is None:
        return value
    return parsed.astimezone(ZoneInfo(timezone_name)).isoformat(timespec="seconds")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _atomic_write_gzip_jsonl(path: Path, rows: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.NamedTemporaryFile("wb", dir=str(path.parent), delete=False) as tmp:
        with gzip.GzipFile(fileobj=tmp, mode="wb", mtime=0) as compressed:
            for row in rows:
                compressed.write(
                    (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                    .encode("utf-8")
                )
                count += 1
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    return count


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_record(path: Path, *, records: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }
    if records is not None:
        item["records"] = records
    return item


def write_pipeline_source_stages(
    *,
    stage_root: Path,
    account_label: str,
    generated_at: datetime,
    since_utc: datetime,
    until_utc: datetime,
    stats: dict[str, int],
    file_audits: list[dict[str, Any]],
    quota_observations: list[WeeklyEvent],
    active_events: list[WeeklyEvent],
    snapshot_audit: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    stage_root.mkdir(parents=True, exist_ok=True)
    validations: list[dict[str, str]] = []

    raw_path = stage_root / "01_raw_inventory.json"
    raw_payload = {
        "schema": PIPELINE_SCHEMA,
        "stage": "raw_inventory",
        "account": account_label,
        "generated_at": _iso_utc(generated_at),
        "range": {"since": _iso_utc(since_utc), "until": _iso_utc(until_utc)},
        "stats": stats,
        "files": file_audits,
    }
    atomic_replace_json(raw_path, raw_payload, sort_keys=False)
    validations.append({
        "check": "raw_files_present",
        "status": "ok" if file_audits else "error",
        "detail": f"{len(file_audits)} rollout files scanned",
    })

    active_coordinates = {
        (event.source_path, event.source_line) for event in active_events
    }
    normalized_events = sorted(
        quota_observations,
        key=lambda item: (item.timestamp, item.source_path, item.source_line),
    )

    def normalized_rows() -> Any:
        for event in normalized_events:
            yield {
            "schema": PIPELINE_SCHEMA,
            "stage": "normalized_weekly_event",
            "account": account_label,
            "timestamp": _iso_utc(event.timestamp),
            "session_id": event.session_id,
            "model": event.model,
            "model_provider": event.model_provider,
            "plan_type": event.plan_type,
            "limit_source": event.limit_source,
            "reset_at": event.reset_at,
            "used_percent": event.used_percent,
            "usage": event.usage,
            "active_limit_observation": (
                event.source_path,
                event.source_line,
            ) in active_coordinates,
            "source": {
                "path": event.source_path,
                "line": event.source_line,
            },
            }

    normalized_path = stage_root / "02_normalized_events.jsonl.gz"
    normalized_count = _atomic_write_gzip_jsonl(normalized_path, normalized_rows())
    unique_coordinates = {
        (event.source_path, event.source_line)
        for event in normalized_events
    }
    validations.append({
        "check": "normalized_source_identity_unique",
        "status": "ok" if len(unique_coordinates) == normalized_count else "error",
        "detail": f"{len(unique_coordinates)} unique source records / {normalized_count} normalized records",
    })
    validations.append({
        "check": "fork_replay_excluded",
        "status": "ok",
        "detail": f"{stats.get('fork_replay_token_counts_excluded', 0)} replayed token_count records excluded",
    })

    snapshots_path = stage_root / "03_daily_snapshots.json"
    snapshots_payload = {
        "schema": PIPELINE_SCHEMA,
        "stage": "daily_05am_snapshots",
        "account": account_label,
        "generated_at": _iso_utc(generated_at),
        "audit": snapshot_audit,
    }
    atomic_replace_json(snapshots_path, snapshots_payload, sort_keys=False)
    validations.append({
        "check": "daily_snapshots_available",
        "status": "ok" if snapshot_audit.get("snapshots_loaded") else "warning",
        "detail": f"{snapshot_audit.get('snapshots_loaded', 0)} healthy snapshots loaded",
    })

    artifacts = [
        _artifact_record(raw_path, records=len(file_audits)),
        _artifact_record(normalized_path, records=normalized_count),
        _artifact_record(
            snapshots_path,
            records=int(snapshot_audit.get("snapshots_loaded") or 0),
        ),
    ]
    return artifacts, validations


def write_pipeline_final_stages(
    *,
    stage_root: Path,
    payload: dict[str, Any],
    report_paths: tuple[Path, Path],
) -> Path:
    aggregate_path = stage_root / "04_aggregate.json"
    atomic_replace_json(aggregate_path, payload, sort_keys=False)
    pipeline = payload.get("pipeline") if isinstance(payload.get("pipeline"), dict) else {}
    validations = pipeline.get("validations") if isinstance(pipeline.get("validations"), list) else []
    artifacts = list(pipeline.get("artifacts") or [])
    artifacts.append(_artifact_record(aggregate_path, records=len(payload.get("daily_intervals", []))))
    for path in report_paths:
        artifacts.append(_artifact_record(path))
    status = "error" if any(item.get("status") == "error" for item in validations) else "ok"
    manifest = {
        "schema": PIPELINE_SCHEMA,
        "account": payload.get("account"),
        "generated_at": payload.get("generated_at"),
        "status": status,
        "validations": validations,
        "artifacts": artifacts,
    }
    manifest_path = stage_root / "manifest.json"
    atomic_replace_json(manifest_path, manifest, sort_keys=False)
    return manifest_path


def _latest_report_paths(
    payload: dict[str, Any],
    output_root: Path,
) -> tuple[Path, Path]:
    account_label = _safe_account_label(payload.get("account"))
    return (
        output_root / f"latest-{account_label}.json",
        output_root / f"latest-{account_label}.md",
    )


def write_outputs(payload: dict[str, Any], output_root: Path, timezone_name: str) -> tuple[Path, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_root / f"codex_weekly_usage_{stamp}.json"
    md_path = output_root / f"codex_weekly_usage_{stamp}.md"
    latest_json_path, latest_md_path = _latest_report_paths(
        payload,
        output_root,
    )
    atomic_replace_json(json_path, payload, sort_keys=False)
    atomic_replace_json(latest_json_path, payload, sort_keys=False)

    rows = payload.get("intervals") if isinstance(payload.get("intervals"), list) else []
    daily_rows = payload.get("daily_intervals") if isinstance(payload.get("daily_intervals"), list) else []
    daily_session_rows = payload.get("daily_session_stats") if isinstance(payload.get("daily_session_stats"), list) else []
    reset_rows = payload.get("inferred_resets") if isinstance(payload.get("inferred_resets"), list) else []
    pipeline = payload.get("pipeline") if isinstance(payload.get("pipeline"), dict) else {}
    lines = [
        "# Codex Weekly Usage",
        "",
        f"Generated at: {payload.get('generated_at')}",
        f"Source: `{payload.get('sessions_root')}`",
        f"Pipeline: `{pipeline.get('stage_root') or ''}` ({pipeline.get('status') or 'not recorded'})",
        "",
        "## Inferred Resets",
        "",
        "| Event (+08, 2026 default) | Kind | From reset_at | To reset_at | Used before | Used after | Evidence |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in reset_rows:
        lines.append(
            "| "
            + " | ".join([
                _local_compact(str(row.get("event_at") or ""), timezone_name),
                str(row.get("reset_kind") or ""),
                _local_compact(str(row.get("from_reset_at") or ""), timezone_name),
                _local_compact(str(row.get("to_reset_at") or ""), timezone_name),
                _fmt_percent(row.get("before_used_percent")),
                _fmt_percent(row.get("after_used_percent")),
                str(row.get("evidence") or ""),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Interval Summary",
        "",
        "| ID | Interval (+08, 2026 default) | Usage | Priced API cost | Estimated 100% usage cost | Status |",
        "|---|---|---:|---:|---:|---|",
    ])
    for row in rows:
        cost = row.get("estimated_api_cost_usd") if isinstance(row.get("estimated_api_cost_usd"), dict) else {}
        start = _local_compact(str(row.get("interval_start_reset_at") or ""), timezone_name)
        end = _local_compact(str(row.get("interval_end_reset_at") or ""), timezone_name)
        hours = _fmt_hours(row.get("interval_days"))
        suffix = ", predict" if row.get("reported_quota_status") == "in_progress" else ""
        lines.append(
            "| "
            + " | ".join([
                str(row.get("interval_id") or ""),
                f"`{start}`~`{end}`, {hours} hours{suffix}",
                _fmt_percent(row.get("reported_used_percent")),
                _fmt_usd(cost.get("total_cost_usd")),
                _fmt_usd(row.get("estimated_full_usage_cost_usd")),
                str(row.get("reported_quota_status") or ""),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Token Stats",
        "",
        "| ID | Input tokens | Cached input tokens | Uncached input tokens | Output tokens | Reasoning output tokens |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for row in rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        input_tokens = _nonnegative_int(usage.get("input_tokens"))
        cached_input_tokens = min(_nonnegative_int(usage.get("cached_input_tokens")), input_tokens)
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        lines.append(
            "| "
            + " | ".join([
                str(row.get("interval_id") or ""),
                _fmt_int(input_tokens),
                _fmt_int(cached_input_tokens),
                _fmt_int(uncached_input_tokens),
                _fmt_int(usage.get("output_tokens")),
                _fmt_int(usage.get("reasoning_output_tokens")),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Daily Summary",
        "",
        "Rows use Beijing time (+08), 2026; each start date covers 05:00 to next 05:00.",
        "Used、Reset day 和 24h usage 优先来自每日 05:00 的 `codex` 直接 limit 快照；token 与价格来自排除 fork 历史重放后的 session raw records。缺少快照时才回退到 session weekly observation。",
        "",
        f"`*` 表示低置信度滚动估算：未同时满足至少 {DAILY_ESTIMATE_MIN_WINDOW_DAYS} 个连续日、累计 limit 变化 >= {DAILY_ESTIMATE_MIN_USAGE_PERCENT:g}%、边界观测延迟 <= {DAILY_ESTIMATE_MAX_ALIGNMENT_LAG_MINUTES:g} 分钟、窗口内无重置。数值仍保留，仅供参考。",
        "",
        "| Start date | Reset day | Used | Obs | 24h usage | Priced API cost | Estimated 100% weekly cost |",
        "|---|---|---:|---|---:|---:|---:|",
    ])
    for row in daily_rows:
        cost = row.get("estimated_api_cost_usd") if isinstance(row.get("estimated_api_cost_usd"), dict) else {}
        alignment = row.get("usage_alignment")
        estimate = _fmt_usd(row.get("estimated_100_percent_weekly_cost_usd"))
        estimate_details = row.get("weekly_equivalent_estimate")
        if estimate and isinstance(estimate_details, dict) and estimate_details.get("confidence") == "low":
            estimate += "*"
        lines.append(
            "| "
            + " | ".join([
                _local_day_compact(str(row.get("day_start_at") or ""), timezone_name),
                _fmt_usage_reset_day(alignment, timezone_name),
                _fmt_usage_used(alignment),
                _fmt_usage_obs(alignment, timezone_name),
                _fmt_percent(row.get("usage_change_used_percent_24h")),
                _fmt_usd(cost.get("total_cost_usd")),
                estimate,
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Daily Token Stats",
        "",
        "Rows use Beijing time (+08), 2026; each start date covers 05:00 to next 05:00.",
        "",
        "| Start date | Uncached input tokens | Uncached input % | Output tokens | Reasoning output % |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in daily_rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        input_tokens = _nonnegative_int(usage.get("input_tokens"))
        cached_input_tokens = min(_nonnegative_int(usage.get("cached_input_tokens")), input_tokens)
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        output_tokens = _nonnegative_int(usage.get("output_tokens"))
        reasoning_output_tokens = min(_nonnegative_int(usage.get("reasoning_output_tokens")), output_tokens)
        lines.append(
            "| "
            + " | ".join([
                _local_day_compact(str(row.get("day_start_at") or ""), timezone_name),
                _fmt_int(uncached_input_tokens),
                _fmt_ratio_percent(uncached_input_tokens, input_tokens),
                _fmt_int(output_tokens),
                _fmt_ratio_percent(reasoning_output_tokens, output_tokens),
            ])
            + " |"
        )
    lines.extend([
        "",
        "## Daily Session Stats",
        "",
        "Rows use Beijing time (+08), 2026; each start date covers 05:00 to next 05:00. Avg cost / user input = Daily Summary priced API cost / user inputs.",
        "",
        "| Start date | Sessions | User inputs | Internal rounds | Avg cost / user input |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in daily_session_rows:
        lines.append(
            "| "
            + " | ".join([
                _local_day_compact(str(row.get("day_start_at") or ""), timezone_name),
                _fmt_int(row.get("session_count")),
                _fmt_int(row.get("user_input_count")),
                _fmt_int(row.get("internal_round_count")),
                _fmt_usd(row.get("average_cost_per_user_input_usd")),
            ])
            + " |"
        )
    lines.extend([
        "",
        "Pricing uses OpenAI API Standard short-context prices per 1M tokens. Cost values are priced subtotals; models listed in each JSON interval's `unpriced_models` are excluded because no matching official API price was configured.",
        "",
    ])
    _atomic_write_text(md_path, "\n".join(lines))
    _atomic_write_text(latest_md_path, "\n".join(lines))
    return json_path, md_path


def _fmt_percent(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _local_compact(value: str, timezone_name: str) -> str:
    if not value:
        return ""
    parsed = _parse_timestamp(value)
    if parsed is None:
        return value
    local = parsed.astimezone(ZoneInfo(timezone_name))
    label = f"{local.strftime('%B')} {local.day} {local:%H:%M}"
    if local.year != DEFAULT_DISPLAY_YEAR:
        label = f"{label}, {local.year}"
    return label


def _local_day_compact(value: str, timezone_name: str) -> str:
    if not value:
        return ""
    parsed = _parse_timestamp(value)
    if parsed is None:
        return value
    local = parsed.astimezone(ZoneInfo(timezone_name))
    label = f"{local.strftime('%B')} {local.day}"
    if local.year != DEFAULT_DISPLAY_YEAR:
        label = f"{label}, {local.year}"
    return label


def _fmt_usd(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _fmt_hours(interval_days: object) -> str:
    try:
        hours = float(interval_days) * 24
    except (TypeError, ValueError, OverflowError):
        return ""
    if abs(hours - round(hours)) < 0.05:
        return f"{round(hours):.0f}"
    return f"{hours:.1f}"


def _fmt_lag_minutes(value: object) -> str:
    if value is None or value == "":
        return ""
    try:
        minutes = float(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)
    if minutes < 60:
        return f"+{minutes:.0f}m"
    return f"+{minutes / 60:.1f}h"


def _fmt_usage_reset_day(value: object, timezone_name: str) -> str:
    if not isinstance(value, dict) or not value.get("reset_at"):
        return ""
    return _local_day_compact(str(value.get("reset_at") or ""), timezone_name)


def _fmt_usage_used(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return _fmt_percent(value.get("used_percent"))


def _fmt_usage_obs(value: object, timezone_name: str) -> str:
    if not isinstance(value, dict) or not value.get("observed_at"):
        return ""
    observed = _local_compact(str(value.get("observed_at") or ""), timezone_name)
    lag = _fmt_lag_minutes(value.get("lag_minutes"))
    return "; ".join(part for part in [f"`{observed}`", lag] if part)


def _fmt_ratio_percent(numerator: object, denominator: object) -> str:
    denominator_int = _nonnegative_int(denominator)
    if denominator_int <= 0:
        return ""
    return f"{_nonnegative_int(numerator) / denominator_int * 100:.1f}%"


def _fmt_int(value: object) -> str:
    return f"{_nonnegative_int(value):_}"


def _parse_reset_arg(raw: str, timezone_name: str) -> int:
    value = raw.strip()
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"invalid reset timestamp {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return int(parsed.astimezone(timezone.utc).timestamp())


def _safe_account_label(value: object) -> str:
    raw = str(value or "").strip()
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in raw)
    return safe.strip("-_") or "default"


def _discover_session_sources(
    *,
    sessions_root: Path | None,
    account_directory: Path,
    accounts: list[str],
) -> list[tuple[str, Path]]:
    if sessions_root is not None:
        expanded = sessions_root.expanduser()
        label = expanded.parent.name if expanded.name == "sessions" else expanded.name
        return [(_safe_account_label(label), expanded)]

    root = account_directory.expanduser()
    if accounts:
        candidates = [(account, root / account / "sessions") for account in accounts]
    else:
        candidates = [
            (path.name, path / "sessions")
            for path in sorted(root.iterdir()) if path.is_dir()
        ] if root.is_dir() else []

    return [
        (_safe_account_label(account), sessions)
        for account, sessions in candidates
        if sessions.is_dir()
    ]


def export_isa_sessions(root: Path, *, account_ids: list[str], since: str, until: str, isa_call=None) -> list[tuple[str, Path]]:
    """Materialize only public usage records in the run's private temporary directory."""
    if isa_call is None:
        from portal_script import isa as isa_call
    if not account_ids:
        account_ids = [str(item["backend_route_id"]) for item in isa_call("ListProviderAccounts").get("accounts", [])
                       if item.get("provider") == "codex" and item.get("backend_route_id")]
    sources = []
    for account in account_ids:
        if _safe_account_label(account) != account:
            raise ValueError("无效的 Codex 账号 ID")
        destination = root / account / "sessions"
        destination.mkdir(parents=True)
        cursor = ""
        while True:
            page = isa_call("GetProviderUsageEvents", account_id=account, since=since, until=until, cursor=cursor, limit=1000)
            for row in page["records"]:
                relative = Path(row["source"])
                if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
                    raise ValueError("用量源文件名无效")
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(row["record"], ensure_ascii=False) + "\n")
            next_cursor = page["next_cursor"]
            if not next_cursor:
                break
            if next_cursor == cursor:
                raise ValueError("用量分页游标未前进")
            cursor = next_cursor
        sources.append((account, destination))
    return sources


def build_payload_for_sessions(
    *,
    account_label: str,
    sessions_root: Path,
    since_utc: datetime,
    until_utc: datetime,
    generated_at: datetime,
    timezone_name: str,
    plan_type_filter: str,
    min_reset_events: int,
    include_empty_intervals: bool,
    include_initial_partial_interval: bool,
    manual_depleted_resets: set[int],
    obsolete_resets: set[int],
    auto_before_min_used: float,
    auto_after_max_used: float,
    auto_min_drop: float,
    manual_merge_window_minutes: int,
    same_reset_drop_min_events: int,
    same_reset_drop_min_sessions: int,
    min_inferred_reset_spacing_hours: int,
    daily_start_hour: int,
    snapshot_root: Path = DEFAULT_DAILY_SNAPSHOT_ROOT,
    stage_root: Path | None = None,
    source_label: str = "",
) -> dict[str, Any]:
    sessions_root = sessions_root.expanduser()
    (
        events,
        quota_observations,
        session_activity_events,
        stats,
        file_audits,
    ) = collect_events(
        sessions_root,
        since_utc=since_utc,
        until_utc=until_utc,
        plan_type_filter=plan_type_filter,
        max_reset_lag_seconds=(WEEKLY_WINDOW_MINUTES * 60) + 3600,
    )
    if source_label:
        for event in events + quota_observations:
            event.source_path = source_label + "/" + Path(event.source_path).relative_to(sessions_root).as_posix()
        for audit in file_audits:
            audit["path"] = source_label + "/" + Path(audit["path"]).relative_to(sessions_root).as_posix()
            audit["source_projection"] = "ISA public usage records"
    daily_snapshots, snapshot_audit = load_daily_limit_snapshots(
        snapshot_root.expanduser(),
        account_id=account_label,
        since_utc=since_utc,
        until_utc=until_utc,
        timezone_name=timezone_name,
        day_start_hour=max(0, min(23, int(daily_start_hour))),
    )
    reset_observations = summarize_reset_observations(quota_observations)
    inferred_resets = infer_reset_events(
        events,
        min_reset_events=max(1, min_reset_events),
        manual_depleted_resets=manual_depleted_resets,
        auto_before_min_used=float(auto_before_min_used),
        auto_after_max_used=float(auto_after_max_used),
        auto_min_drop=float(auto_min_drop),
        manual_merge_window_seconds=max(0, int(manual_merge_window_minutes)) * 60,
        timezone_name=timezone_name,
        same_reset_drop_min_events=max(1, int(same_reset_drop_min_events)),
        same_reset_drop_min_sessions=max(1, int(same_reset_drop_min_sessions)),
        min_inferred_reset_spacing_hours=max(0, int(min_inferred_reset_spacing_hours)),
    )
    intervals = summarize_intervals_from_inferred_resets(
        quota_observations,
        inferred_resets,
        include_initial_partial_interval=include_initial_partial_interval,
        generated_at=generated_at,
    )
    daily_intervals = summarize_daily_intervals(
        events,
        quota_observations,
        inferred_resets,
        generated_at=generated_at,
        range_until_utc=until_utc,
        timezone_name=timezone_name,
        day_start_hour=max(0, min(23, int(daily_start_hour))),
        daily_snapshots=daily_snapshots,
    )
    annotate_daily_weekly_equivalent_estimates(daily_intervals)
    daily_session_stats = summarize_daily_session_stats(session_activity_events, daily_intervals)
    _, reset_buckets = summarize_intervals(
        events,
        quota_observations,
        min_reset_events=max(1, min_reset_events),
        include_empty_intervals=include_empty_intervals,
        include_initial_partial_interval=False,
        generated_at=generated_at,
        manual_depleted_resets=manual_depleted_resets,
        obsolete_resets=obsolete_resets,
    )
    payload = {
        "schema_version": 1,
        "account": account_label,
        "generated_at": _iso_utc(generated_at),
        "timezone": timezone_name,
        "sessions_root": source_label or str(sessions_root),
        "range": {
            "since": _iso_utc(since_utc),
            "until": _iso_utc(until_utc),
        },
        "filters": {
            "weekly_window_minutes": WEEKLY_WINDOW_MINUTES,
            "weekly_window_source": "first rate_limits primary/secondary entry whose window_minutes is 10080",
            "ignored_window_source": "rate_limits entries with non-weekly window_minutes, including the historical 300-minute 5h window",
            "plan_type": plan_type_filter,
            "min_reset_events": max(1, min_reset_events),
            "include_empty_intervals": bool(include_empty_intervals),
            "include_initial_partial_interval": bool(include_initial_partial_interval),
            "same_utc_day_reset_bucket_rule": "when multiple weekly reset buckets pass the threshold on the same UTC date, keep a manual-depleted bucket if present, otherwise keep the bucket with the most observations",
            "manual_depleted_reset_at": sorted(_iso_utc(datetime.fromtimestamp(item, timezone.utc)) for item in manual_depleted_resets),
            "obsolete_reset_at": sorted(_iso_utc(datetime.fromtimestamp(item, timezone.utc)) for item in obsolete_resets),
            "reset_at_rule": "all weekly-window reset_at observations are summarized; reset_at anchors the reset date as reset_at minus the weekly window, while usage/reset_at observations choose the hour",
            "manual_reset_inference": "explicit manual/depleted markers override a nearby low-usage reset; other reset_at changes are diagnostic unless usage confirms a reset",
            "automatic_reset_inference": "a reset is inferred when a durable active reset_at bucket starts at low used_percent, usage drops from high to low while reset_at changes, or usage drops from high to low without reset_at changing but the hour has multi-session support",
            "close_inferred_reset_rule": "inferred reset candidates closer than min_inferred_reset_spacing_hours are collapsed, keeping the strongest evidence",
            "reset_event_time_precision": "hour",
            "auto_reset_before_min_used": float(auto_before_min_used),
            "auto_reset_after_max_used": float(auto_after_max_used),
            "auto_reset_min_drop": float(auto_min_drop),
            "manual_merge_window_minutes": max(0, int(manual_merge_window_minutes)),
            "same_reset_drop_min_events": max(1, int(same_reset_drop_min_events)),
            "same_reset_drop_min_sessions": max(1, int(same_reset_drop_min_sessions)),
            "min_inferred_reset_spacing_hours": max(0, int(min_inferred_reset_spacing_hours)),
            "daily_start_hour_local": max(0, min(23, int(daily_start_hour))),
            "fork_replay_filter": f"for rollout files whose physical session_meta has forked_from_id, records in the first {FORK_REPLAY_BURST_SECONDS:g}s import burst are excluded and accepted records retain the physical file session identity",
            "daily_usage_alignment": "each daily interval starts at the configured local hour; Used and 24h usage prefer the direct daily 05:00 codex limit snapshots, while token/cost totals use normalized non-replayed session token_count records",
            "daily_weekly_equivalent_estimate": {
                "method": "up to 3 consecutive daily costs divided by their cumulative weekly limit change, scaled to 100%",
                "minimum_consecutive_days_for_high_confidence": DAILY_ESTIMATE_MIN_WINDOW_DAYS,
                "minimum_cumulative_usage_change_percent_for_high_confidence": DAILY_ESTIMATE_MIN_USAGE_PERCENT,
                "maximum_boundary_observation_lag_minutes_for_high_confidence": DAILY_ESTIMATE_MAX_ALIGNMENT_LAG_MINUTES,
                "requires_no_reset_for_high_confidence": True,
                "low_confidence_display": "retain the numeric estimate and append * in Markdown",
            },
            "daily_session_stats": "user_input_count counts event_msg user_message records; internal_round_count counts weekly token_count observations used for daily token and cost totals",
        },
        "api_pricing": {
            "source": PRICING_SOURCE_URL,
            "basis": "OpenAI API Standard short-context prices per 1M tokens",
            "prices_per_1m_tokens": API_PRICE_PER_1M,
            "model_price_aliases": MODEL_PRICE_ALIASES,
            "note": "Cached input tokens are charged at the cached input rate; reasoning_output_tokens are included in output_tokens and are not billed a second time. Models missing from prices_per_1m_tokens are reported as unpriced_models and excluded from cost subtotals.",
        },
        "stats": stats,
        "snapshot_audit": snapshot_audit,
        "all_reset_observations": reset_observations,
        "inferred_resets": inferred_resets,
        "reset_buckets": reset_buckets,
        "intervals": intervals,
        "daily_intervals": daily_intervals,
        "daily_session_stats": daily_session_stats,
    }
    if stage_root is not None:
        artifacts, validations = write_pipeline_source_stages(
            stage_root=stage_root,
            account_label=account_label,
            generated_at=generated_at,
            since_utc=since_utc,
            until_utc=until_utc,
            stats=stats,
            file_audits=file_audits,
            quota_observations=quota_observations,
            active_events=events,
            snapshot_audit=snapshot_audit,
        )
        payload["pipeline"] = {
            "schema": PIPELINE_SCHEMA,
            "stage_root": str(stage_root),
            "artifacts": artifacts,
            "validations": validations,
        }
        invalid_daily_rows = [
            row for row in daily_intervals
            if row.get("usage_change_status")
            == "used_percent_decreased_without_reset_at_change"
        ]
        validations.append({
            "check": "daily_snapshot_transitions_consistent",
            "status": "error" if invalid_daily_rows else "ok",
            "detail": f"{len(invalid_daily_rows)} unexplained used_percent decreases",
        })
        payload["pipeline"]["status"] = (
            "error"
            if any(item.get("status") == "error" for item in validations)
            else "ok"
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize Codex token usage between adjacent weekly quota reset buckets.")
    parser.add_argument("--isa", action="store_true", help="Read sanitized usage observations through the RunScript ISA socket.")
    parser.add_argument("--sessions-root", type=Path, default=None, help="Scan one explicit Codex sessions directory. If omitted, scan account sessions under --account-directory separately.")
    parser.add_argument("--account-directory", type=Path, default=DEFAULT_CODEX_DIRECTORY, help="Root containing registered Codex account directories.")
    parser.add_argument("--account", action="append", default=[], help="Account directory name under --account-directory to scan. Can be repeated. Defaults to every account with a sessions directory.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_DAILY_SNAPSHOT_ROOT, help="Root containing YYYY-MM-DD direct 05:00 limit snapshots.")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--since", type=_date_arg, default=None, help="Local start date, inclusive. Defaults to 45 days before --until.")
    parser.add_argument("--until", type=_date_arg, default=None, help="Local end date, exclusive. Defaults to tomorrow.")
    parser.add_argument("--plan-type", default="", help="Filter by rate_limits.plan_type. Defaults to all plan types.")
    parser.add_argument("--min-reset-events", type=int, default=1000, help="Minimum observations required for a reset bucket.")
    parser.add_argument("--include-empty-intervals", action="store_true", help="Keep adjacent reset intervals that have no token_count events assigned to the later reset bucket.")
    parser.add_argument("--include-initial-partial-interval", action="store_true", help="Include observations from the first matching weekly event up to the first selected reset bucket.")
    parser.add_argument("--manual-depleted-reset-at", action="append", default=[], help="Reset timestamp for a bucket known to have been manually reset after depletion. Accepts epoch seconds or ISO timestamp.")
    parser.add_argument("--obsolete-reset-at", action="append", default=[], help="Reset timestamp for a stale weekly bucket that should not form an interval boundary. Accepts epoch seconds or ISO timestamp.")
    parser.add_argument("--auto-reset-before-min-used", type=float, default=20.0, help="Minimum previous weekly used_percent for automatic reset inference.")
    parser.add_argument("--auto-reset-after-max-used", type=float, default=5.0, help="Maximum new weekly used_percent for automatic reset inference.")
    parser.add_argument("--auto-reset-min-drop", type=float, default=20.0, help="Minimum used_percent drop for automatic reset inference.")
    parser.add_argument("--manual-merge-window-minutes", type=int, default=120, help="Merge an explicit manual reset marker with an inferred low-usage reset within this window.")
    parser.add_argument("--same-reset-drop-min-events", type=int, default=20, help="Minimum low-usage observations in an hour for a same-reset_at usage-drop reset candidate.")
    parser.add_argument("--same-reset-drop-min-sessions", type=int, default=3, help="Minimum sessions in an hour for a same-reset_at usage-drop reset candidate.")
    parser.add_argument("--min-inferred-reset-spacing-hours", type=int, default=36, help="Collapse inferred reset candidates closer than this many hours, keeping the strongest evidence.")
    parser.add_argument("--daily-start-hour", type=int, default=5, help="Local hour used as the daily usage boundary and /usage alignment point.")
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--no-stages", action="store_true", help="Write only the final JSON/Markdown report, without auditable intermediate artifacts.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when a pipeline validation has status=error.")
    args = parser.parse_args(argv)

    tz = ZoneInfo(args.timezone)
    until_day = args.until or (datetime.now(tz).date() + timedelta(days=1))
    since_day = args.since or (until_day - timedelta(days=45))
    since_utc = datetime.combine(since_day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    until_utc = datetime.combine(until_day, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)

    generated_at = datetime.now(timezone.utc)
    manual_depleted_resets = {
        value for value in (_parse_reset_arg(raw, args.timezone) for raw in args.manual_depleted_reset_at)
        if value
    }
    obsolete_resets = {
        value for value in (_parse_reset_arg(raw, args.timezone) for raw in args.obsolete_reset_at)
        if value
    }
    # The temporary export is private to this script run and is always removed.
    with tempfile.TemporaryDirectory(prefix="codex-usage-") as export_directory:
        if args.isa:
            if args.sessions_root is not None or args.account_directory != DEFAULT_CODEX_DIRECTORY:
                parser.error("--isa 不接受本机账号目录")
            sources = export_isa_sessions(Path(export_directory), account_ids=args.account,
                                          since=_iso_utc(since_utc), until=_iso_utc(until_utc))
        else:
            sources = _discover_session_sources(
                sessions_root=args.sessions_root,
                account_directory=args.account_directory,
                accounts=[item.strip() for item in args.account if item.strip()],
            )
        if not sources:
            raise SystemExit(f"no Codex session sources found under {args.account_directory.expanduser()}")

        payloads = []
        pipeline_failed = False
        for account_label, sessions_root in sources:
            account_output_root = args.output_root.expanduser() / account_label
            stage_root = None
            if not args.no_write and not args.no_stages:
                stage_root = account_output_root / "stages" / "latest"
            payload = build_payload_for_sessions(
                account_label=account_label,
                sessions_root=sessions_root,
                since_utc=since_utc,
                until_utc=until_utc,
                generated_at=generated_at,
                timezone_name=args.timezone,
                plan_type_filter=args.plan_type.strip(),
                min_reset_events=max(1, args.min_reset_events),
                include_empty_intervals=args.include_empty_intervals,
                include_initial_partial_interval=args.include_initial_partial_interval,
                manual_depleted_resets=manual_depleted_resets,
                obsolete_resets=obsolete_resets,
                auto_before_min_used=float(args.auto_reset_before_min_used),
                auto_after_max_used=float(args.auto_reset_after_max_used),
                auto_min_drop=float(args.auto_reset_min_drop),
                manual_merge_window_minutes=max(0, int(args.manual_merge_window_minutes)),
                same_reset_drop_min_events=max(1, int(args.same_reset_drop_min_events)),
                same_reset_drop_min_sessions=max(1, int(args.same_reset_drop_min_sessions)),
                min_inferred_reset_spacing_hours=max(0, int(args.min_inferred_reset_spacing_hours)),
                daily_start_hour=max(0, min(23, int(args.daily_start_hour))),
                snapshot_root=args.snapshot_root,
                stage_root=stage_root,
                source_label=f"isa:GetProviderUsageEvents:{account_label}" if args.isa else "",
            )
            payloads.append(payload)

            if not args.no_write:
                json_path, md_path = write_outputs(payload, account_output_root, args.timezone)
                print(f"wrote {json_path}")
                print(f"wrote {md_path}")
                if stage_root is not None:
                    latest_json_path, latest_md_path = _latest_report_paths(
                        payload,
                        account_output_root,
                    )
                    manifest_path = write_pipeline_final_stages(
                        stage_root=stage_root,
                        payload=payload,
                        report_paths=(latest_json_path, latest_md_path),
                    )
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    pipeline_failed = pipeline_failed or manifest.get("status") == "error"
                    print(f"wrote {manifest_path}")

        if args.stdout or args.no_write:
            output = payloads[0] if len(payloads) == 1 else {"accounts": payloads}
            print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1 if args.strict and pipeline_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

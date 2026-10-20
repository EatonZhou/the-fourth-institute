from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from management_portal import usage_read_model
from management_portal.execution.contracts import usage_total

from . import execution_client, lineage_primitives, resource_client


TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0




def _sum_usage(
    left: dict[str, int],
    right: dict[str, Any],
    *,
    include_cached_input: bool,
) -> dict[str, int]:
    output = {
        key: _nonnegative_int(left.get(key)) + _nonnegative_int(right.get(key))
        for key in TOKEN_KEYS
        if key != "total_tokens"
    }
    output["total_tokens"] = _nonnegative_int(
        left.get("total_tokens")
    ) + usage_total(right, include_cached_input=include_cached_input)
    return {key: value for key, value in output.items() if value > 0}


def _query_windows(now: datetime) -> list[tuple[str, str]]:
    today = now.date()
    # The current week can cross the year boundary.
    earliest = min(usage_read_model.period_starts(today).values())
    start = datetime.combine(earliest, datetime.min.time(), tzinfo=now.tzinfo)
    end = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    cursor = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    windows: list[tuple[str, str]] = []
    while cursor < end_utc:
        chunk_end = min(cursor + timedelta(days=62), end_utc)
        windows.append(
            (
                cursor.isoformat(timespec="seconds").replace("+00:00", "Z"),
                chunk_end.isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
        )
        cursor = chunk_end
    return windows


def _parse_bucket(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def _empty_variant() -> dict[str, Any]:
    return {
        "periods": {"today": 0, "week": 0, "month": 0, "year": 0},
        "period_others": {"today": 0, "week": 0, "month": 0, "year": 0},
        "daily_assigned": {},
        "daily_others": {},
        "daily_tracked": {},
        "daily_by_provider": {},
        "daily_by_provider_utc": {},
        "daily_by_resident": {},
        "hourly_today_assigned": {},
        "hourly_today_by_resident": {},
        "hourly_today_others": {},
        "resident_titles": {},
        "resident_lineages": {},
        "lineage_titles": {},
        "resident_projects": {},
        "project_titles": {},
        "residents": [],
    }


def _visible_roots(actor: str) -> dict[str, dict[str, Any]]:
    return resource_client.visible_resident_usage_roots(actor=actor)


def _build_variant(
    rows: list[dict[str, Any]],
    visible: dict[str, dict[str, Any]],
    *,
    include_cached_input: bool,
    now: datetime,
) -> dict[str, Any]:
    today = now.date()
    starts = usage_read_model.period_starts(today)
    payload = _empty_variant()
    payload["today_start"] = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    payload["resident_titles"] = {
        str(record.get("resident_id") or resident_id): str(
            record.get("title") or resident_id
        ).strip()
        for resident_id, record in visible.items()
    }
    payload["resident_lineages"] = {
        str(record.get("resident_id") or resident_id): str(record["lineage_id"])
        for resident_id, record in visible.items()
        if record.get("lineage_id")
    }
    payload["lineage_titles"] = {
        str(record["lineage_id"]): str(record.get("lineage_title") or record["lineage_id"])
        for record in visible.values()
        if record.get("lineage_id")
    }
    payload["resident_projects"] = {
        str(record.get("resident_id") or resident_id): str(record["project_id"])
        for resident_id, record in visible.items()
        if record.get("project_id")
    }
    payload["project_titles"] = {
        str(record["project_id"]): str(record.get("project_title") or record["project_id"])
        for record in visible.values()
        if record.get("project_id")
    }
    item_periods: dict[str, dict[str, int]] = {}
    item_usage: dict[str, dict[str, int]] = {}

    for row in rows:
        occurred_at = _parse_bucket(row.get("bucket_hour"))
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        tokens = usage_total(
            usage,
            include_cached_input=include_cached_input,
        )
        if occurred_at is None or not tokens:
            continue
        day = occurred_at.date()
        day_key = day.isoformat()
        hour_key = f"{occurred_at.hour:02d}:00"
        session_resident_id = str(row.get("resident_id") or "").strip()
        resident = visible.get(session_resident_id)
        resident_id = str(
            (resident or {}).get("resident_id") or session_resident_id
        ).strip()
        is_visible = resident is not None and bool(resident_id)
        is_registered = bool(
            lineage_primitives.RESIDENT_ID_RE.fullmatch(session_resident_id)
        )

        if is_registered:
            payload["daily_assigned"][day_key] = (
                payload["daily_assigned"].get(day_key, 0) + tokens
            )
            if day == today:
                payload["hourly_today_assigned"][hour_key] = (
                    payload["hourly_today_assigned"].get(hour_key, 0) + tokens
                )
            for key, start in starts.items():
                if day >= start:
                    payload["periods"][key] += tokens
            if is_visible:
                daily = payload["daily_by_resident"].setdefault(day_key, {})
                daily[resident_id] = daily.get(resident_id, 0) + tokens
                if day == today:
                    hourly = payload["hourly_today_by_resident"].setdefault(
                        hour_key,
                        {},
                    )
                    hourly[resident_id] = hourly.get(resident_id, 0) + tokens
                periods = item_periods.setdefault(
                    resident_id,
                    {key: 0 for key in starts},
                )
                for key, start in starts.items():
                    if day >= start:
                        periods[key] += tokens
                item_usage[resident_id] = _sum_usage(
                    item_usage.get(resident_id, {}),
                    usage,
                    include_cached_input=include_cached_input,
                )
        else:
            payload["daily_others"][day_key] = (
                payload["daily_others"].get(day_key, 0) + tokens
            )
            if day == today:
                payload["hourly_today_others"][hour_key] = (
                    payload["hourly_today_others"].get(hour_key, 0) + tokens
                )
            for key, start in starts.items():
                if day >= start:
                    payload["period_others"][key] += tokens

        payload["daily_tracked"][day_key] = (
            payload["daily_tracked"].get(day_key, 0) + tokens
        )
        provider = str(row["provider"])
        provider_day = payload["daily_by_provider"].setdefault(day_key, {})
        provider_day[provider] = provider_day.get(provider, 0) + tokens
        utc_day = occurred_at.astimezone(timezone.utc).date().isoformat()
        utc_provider_day = payload["daily_by_provider_utc"].setdefault(utc_day, {})
        utc_provider_day[provider] = utc_provider_day.get(provider, 0) + tokens

    payload["residents"] = [
        {
            "resident_id": resident_id,
            "title": payload["resident_titles"].get(resident_id, resident_id),
            "periods": periods,
            "total_usage": item_usage.get(resident_id, {}),
        }
        for resident_id, periods in item_periods.items()
        if any(periods.values())
    ]
    payload["residents"].sort(
        key=lambda item: item.get("periods", {}).get("year", 0),
        reverse=True,
    )
    return payload


def collect(*, actor: str) -> dict[str, Any]:
    now = _local_now()
    try:
        rows: list[dict[str, Any]] = []
        updated_at = ""
        read_windows = []
        for start_hour, end_hour in _query_windows(now):
            read_started_at = usage_read_model.collection_time()
            aggregate = execution_client.token_usage(
                start_hour=start_hour,
                end_hour=end_hour,
                provider="*",
                include_accounts=True,
            )
            read_windows.append({"start": start_hour, "end": end_hour,
                                 "read_started_at": read_started_at,
                                 "read_finished_at": usage_read_model.collection_time(), "status": "ok"})
            rows.extend(
                dict(row)
                for row in aggregate.get("rows") or []
                if isinstance(row, dict)
            )
            updated_at = max(
                updated_at,
                str(aggregate.get("updated_at") or ""),
            )
        visible = _visible_roots(str(actor or ""))
    except Exception as exc:
        result = _empty_variant()
        result["excluding_cached_input"] = _empty_variant()
        result.update(
            {
                "status": "error",
                "error": f"{exc.__class__.__name__}: {exc}"[:1000],
                "refreshing": False,
                "updated_at": "",
            }
        )
        return result

    providers = sorted({str(row["provider"]) for row in rows})

    def variant(include_cached_input: bool) -> dict[str, Any]:
        payload = _build_variant(
            rows, visible, include_cached_input=include_cached_input, now=now,
        )
        payload["providers"] = providers
        return payload

    result = variant(True)
    result["excluding_cached_input"] = variant(False)
    result.update(
        {
            "status": "ok",
            "error": "",
            "refreshing": False,
            "updated_at": updated_at or _now_iso(),
            "collection_windows": read_windows,
        }
    )
    # Project the same authorized Resident/Lineage view for each ledger scope.
    # Empty account IDs are retained explicitly; current bindings cannot recover them.
    grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for row in rows:
        provider = str(row["provider"])
        account = str(row.get("account_id") or "")
        grouped.setdefault((provider, None), []).append(row)
        grouped.setdefault((provider, account), []).append(row)
    result["scopes"] = [
        {"provider": provider, "account_id": account,
         **_build_variant(items, visible, include_cached_input=True, now=now),
         "excluding_cached_input": _build_variant(items, visible, include_cached_input=False, now=now)}
        for (provider, account), items in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or ""))
    ]
    return result

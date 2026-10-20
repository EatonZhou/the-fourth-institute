"""Content-free observation metadata and complete authorized usage buckets."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def collection_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def period_starts(today: date) -> dict[str, date]:
    """First day of each Home usage window that contains ``today``."""
    return {
        "today": today,
        "week": today - timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "year": today.replace(month=1, day=1),
    }


def bucket_groups(buckets: dict[str, dict[str, int]]) -> list[dict]:
    """The caller supplies already authorized IDs; the empty ID is anonymous."""
    return [
        {"bucket": bucket, "groups": [
            {"id": key, "total_tokens": tokens, **({"anonymous": True} if not key else {})}
            for key, tokens in sorted(groups.items(), key=lambda item: (not bool(item[0]), item[0]))
            if tokens
        ]}
        for bucket, groups in sorted(buckets.items())
    ]

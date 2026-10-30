"""Resource Backup run records: normalizing the service's run documents and picking the
active run and the recent runs."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from management_portal.backup_lineage import BACKUP_OPERATIONS, VERIFICATION_OPERATIONS

from management_portal.resource_backup_values import (
    BACKUP_LABELS, VERIFICATION_LABELS, _mapping, _string, _number, _boolean, _safe_identifier, _timestamp,
    _run_status, _named_record,
)


_MAX_RECENT_RUNS = 20
_TERMINAL_RUN_STATUSES = {
    "cancelled",
    "completed",
    "degraded",
    "failed",
    "incomplete",
    "skipped",
}


def _active_run(
    status: Mapping[str, Any],
    run_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    candidates: list[Mapping[str, Any]] = []
    explicit = status.get("active_run") or status.get("current_run")
    if isinstance(explicit, Mapping):
        candidates.append(explicit)
    current_run_id = status.get("current_run_id")
    if current_run_id:
        operation = _string(
            status.get("current_operation"),
            limit=80,
        ).casefold()
        profile = next(
            (
                value
                for value in BACKUP_OPERATIONS
                if value in operation
            ),
            "",
        )
        mode = next(
            (
                value
                for value in ("deep-check", "check")
                if value in operation
            ),
            "",
        )
        profile_state = _named_record(
            status.get("profiles"),
            profile,
        )
        maintenance_state = _named_record(
            status.get("maintenance"),
            mode,
        )
        candidates.append({
            "run_id": current_run_id,
            "kind": "verification" if mode else "backup",
            "profile": profile,
            "mode": mode,
            "state": status.get("state"),
            "phase": status.get("phase") or status.get("state"),
            "started_at": (
                status.get("started_at")
                or profile_state.get("last_attempt_at")
                or maintenance_state.get("last_attempt_at")
            ),
        })
    if status.get("run_id") and status.get("status"):
        candidates.append(status)
    candidates.extend(run_records)
    normalized = [
        _normalize_run(item)
        for item in candidates
        if isinstance(item, Mapping)
    ]
    active = [
        item
        for item in normalized
        if item.get("run_id")
        and item.get("status") not in _TERMINAL_RUN_STATUSES
        and item.get("status") != "unknown"
    ]
    if not active:
        return None
    return max(active, key=_run_sort_key)


def _recent_runs(
    status: Mapping[str, Any],
    run_records: Sequence[Mapping[str, Any]],
    active_run: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    candidates: list[Mapping[str, Any]] = list(run_records)
    raw_recent = status.get("recent_runs") or status.get("runs")
    if isinstance(raw_recent, list):
        candidates.extend(
            item for item in raw_recent if isinstance(item, Mapping)
        )
    by_id: dict[str, dict[str, Any]] = {}
    for item in candidates:
        normalized = _normalize_run(item)
        run_id = normalized.get("run_id") or ""
        if not run_id:
            continue
        previous = by_id.get(run_id)
        if previous is None or _run_sort_key(normalized) >= _run_sort_key(previous):
            by_id[run_id] = normalized
    if active_run:
        by_id.pop(str(active_run.get("run_id") or ""), None)
    return sorted(
        by_id.values(),
        key=_run_sort_key,
        reverse=True,
    )[:_MAX_RECENT_RUNS]


def _normalize_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    progress = _mapping(raw.get("progress"))
    verification = _mapping(raw.get("verification"))
    summary = _mapping(raw.get("summary"))
    kind = _string(raw.get("kind") or raw.get("operation"), limit=40).casefold()
    if kind not in {"backup", "verification"}:
        kind = "verification" if (
            raw.get("mode")
            or summary.get("operation") in VERIFICATION_OPERATIONS
            or raw.get("command") == "maintain"
        ) else "backup"
    profile = _string(
        raw.get("profile") or summary.get("profile"),
        limit=40,
    ).casefold()
    mode = _string(
        raw.get("mode")
        or summary.get("operation")
        or (raw.get("operation") if kind == "verification" else ""),
        limit=40,
    ).casefold()
    status = _run_status(raw.get("status") or raw.get("state"))
    if kind == "verification":
        run_label = VERIFICATION_LABELS.get(mode, "仓库校验")
        summaries = {
            "completed": "校验已完成",
            "degraded": "校验部分完成",
            "incomplete": "校验不完整",
            "failed": "校验未完成",
            "cancelled": "校验已取消",
            "skipped": "校验已跳过",
            "queued": "等待后台校验",
            "running": "正在后台校验",
            "unknown": "校验状态尚未上报",
        }
    else:
        run_label = BACKUP_LABELS.get(profile, "备份任务")
        summaries = {
            "completed": "备份已完成",
            "degraded": "备份部分完成",
            "incomplete": "备份不完整",
            "failed": "备份未完成",
            "cancelled": "备份已取消",
            "skipped": "备份已跳过",
            "queued": "等待后台执行",
            "running": "正在后台执行",
            "unknown": "状态尚未上报",
        }
    return {
        "run_id": _safe_identifier(raw.get("run_id") or raw.get("id")),
        "kind": kind,
        "profile": profile if profile in BACKUP_OPERATIONS else "",
        "profile_label": run_label,
        "mode": mode if mode in VERIFICATION_OPERATIONS else "",
        "status": status,
        "phase": _phase(raw.get("phase")),
        "summary": summaries[status],
        "created_by": _string(
            raw.get("created_by")
            or raw.get("actor"),
            limit=120,
        ),
        "created_at": _timestamp(raw.get("created_at")),
        "started_at": _timestamp(raw.get("started_at")),
        "finished_at": _timestamp(
            raw.get("finished_at")
            or raw.get("completed_at")
        ),
        "progress": {
            "items_done": _number(
                progress.get("items_done")
                or raw.get("items_done")
            ),
            "items_total": _number(
                progress.get("items_total")
                or raw.get("items_total")
            ),
            "bytes_done": _number(
                progress.get("bytes_done")
                or raw.get("bytes_done")
            ),
            "bytes_total": _number(
                progress.get("bytes_total")
                or raw.get("bytes_total")
            ),
        },
        "snapshot_id": _safe_identifier(
            raw.get("snapshot_id")
            or raw.get("snapshot")
            or summary.get("snapshot_id")
        ),
        "verification": {
            "status": _run_status(
                verification.get("status")
                or raw.get("verification_status")
            ),
            "checked_at": _timestamp(
                verification.get("checked_at")
                or raw.get("verified_at")
            ),
        },
        "error": _safe_error(
            raw.get("error")
            or (
                (raw.get("errors") or [None])[0]
                if isinstance(raw.get("errors"), list)
                else None
            )
        ),
    }


def _phase(value: object) -> str:
    normalized = _string(value, limit=60).casefold().replace(" ", "_").replace("-", "_")
    allowed = {
        "planning",
        "preparing",
        "backing_up",
        "snapshotting",
        "verifying",
        "replicating",
        "finalizing",
        "completed",
    }
    return normalized if normalized in allowed else ""


def _safe_error(value: object) -> dict[str, Any] | None:
    if not value:
        return None
    raw = _mapping(value)
    code = _safe_identifier(raw.get("code") if raw else value)
    return {
        "code": code or "backup_failed",
        "message": "The Resource Backup operation did not complete.",
        "retryable": _boolean(raw.get("retryable")) if raw else False,
    }


def _run_sort_key(run: Mapping[str, Any]) -> str:
    return str(
        run.get("finished_at")
        or run.get("started_at")
        or run.get("created_at")
        or ""
    )

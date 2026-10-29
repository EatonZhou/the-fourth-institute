from __future__ import annotations

import hashlib
from typing import Any

from management_portal.execution.accounts import shared_account_configuration
from management_portal.notifications.subjects import business_projection_subject
from management_portal.resident_avatar import get_avatar, projection_fields, SPRITE_RARITIES

from . import (
    execution_client,
    resident_backend,
    resident_catalog,
    resident_protocol,
)
from management_common.clock import utc_now_iso


def _resident_account_id(
    record: dict[str, Any],
) -> str:
    return str(resident_backend.execution_account(record).get("account_id") or "other")


def _run(
    record: dict[str, Any],
    *,
    status: str,
    account_id: str,
    message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message = message or {}
    resident_id = str(record.get("resident_id") or "").strip()
    lineage_id = str(record.get("lineage_id") or "").strip()
    title = str(record.get("title") or resident_id or "Resident").strip()
    message_identity = next(
        (
            str(message.get(key) or "").strip()
            for key in (
                "message_id",
                "created_at",
            )
            if str(message.get(key) or "").strip()
        ),
        "unknown",
    )
    return {
        "type": "resident",
        "status": status,
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        "title": title,
        "session_title": title,
        "account_id": str(account_id or "other"),
        "_resource_id": resident_id,
        "_sprite_binding_key": ":".join((resident_id, message_identity)),
    }


def _run_sort_key(run: dict[str, Any]) -> tuple[str, int, str, str, str]:
    return (
        str(run.get("account_id") or "other"),
        0 if str(run.get("status") or "") == "running" else 1,
        str(run.get("resident_id") or ""),
        str(run.get("title") or ""),
        str(run.get("_sprite_binding_key") or ""),
    )


def _capacity_snapshot() -> tuple[int, dict[str, int]]:
    try:
        accounts = execution_client.service().accounts().get("accounts", [])
        account_limits = {item["account_id"]: max(1, int(item.get("max_concurrency") or 1)) for item in accounts}
        return max(1, sum(account_limits.values())), account_limits
    except Exception:
        return 1, {}


def _resident_runs(
    record: dict[str, Any],
    activity: dict[str, Any],
) -> list[dict[str, Any]]:
    work_state = str(activity.get("status") or "idle").strip().lower()
    active_status = (
        "running"
        if work_state == "queued" and activity.get("work_phase") == "starting_session"
        else work_state
    )
    queue_items = [
        dict(item)
        for item in activity.get("queued_messages") or []
        if isinstance(item, dict)
    ]
    if work_state not in {"queued", "running", "stopping"} and not queue_items:
        # Idle Residents have no visible run and need no session binding read.
        return []
    account_id = _resident_account_id(record)
    active_message = dict(activity.get("active_message") or {})
    active_message.setdefault("message_id", str(activity.get("message_id") or ""))
    active_message_id = str(active_message.get("message_id") or "").strip()
    runs: list[dict[str, Any]] = []
    if work_state in {"queued", "running", "stopping"} and active_message_id:
        runs.append(_run(
            record,
            status=active_status,
            account_id=account_id,
            message=active_message,
        ))
    for item in queue_items:
        if str(item.get("message_id") or "").strip() == active_message_id:
            continue
        runs.append(_run(record, status="queued", account_id=account_id, message=item))
    if work_state == "queued" and not runs:
        runs.append(_run(record, status=active_status, account_id=account_id, message=active_message))
    return runs


def publish_changed(publisher: object, resident_id: str) -> None:
    """Tell subscribers to re-read the authoritative Resident projection."""

    publisher.publish(
        business_projection_subject("active-agents", "snapshot"),
        {"kind": "changed", "fields": [str(resident_id or "").strip()]},
    )


def _actor_visible_run(
    source: dict[str, Any],
    *,
    readable: bool,
) -> dict[str, Any]:
    run = dict(source)
    if readable:
        return run
    run.pop("resident_id", None)
    run.pop("lineage_id", None)
    run.pop("session_title", None)
    run["title"] = "Agent"
    return run


def collect(*, actor: str = "") -> dict[str, Any]:
    """Build the Home activity view directly from canonical Resident state."""

    catalog = resident_catalog.get_catalog()
    records = catalog.get_identity_records(catalog.state_store.resident_work_ids())
    errors: list[str] = []
    try:
        activity = resident_protocol.read_resident_activity(
            str(record.get("resident_id") or "")
            for record in records
            if str(record.get("resident_id") or "")
        )
    except Exception as exc:
        activity = {}
        errors.append(f"{exc.__class__.__name__}: {exc}")

    runs: list[dict[str, Any]] = []
    with shared_account_configuration():
        for record in records:
            resident_id = str(record.get("resident_id") or "").strip()
            if not resident_id:
                continue
            try:
                runs.extend(
                    _resident_runs(record, dict(activity.get(resident_id) or {}))
                )
            except Exception as exc:
                errors.append(f"{resident_id}: {exc}")
    runs.sort(key=_run_sort_key)
    max_concurrency, account_limits = _capacity_snapshot()

    resource_ids = sorted({
        str(run.get("_resource_id") or "")
        for run in runs
        if str(run.get("_resource_id") or "")
    })
    try:
        from management_portal.permission_system import readable_ids
        visible_ids = readable_ids("GetResident", "resident_id", resource_ids, actor=actor, entry="system")
    except Exception as exc:
        visible_ids = set()
        errors.append(f"{exc.__class__.__name__}: {exc}")
    actor_visible_runs = [
        _actor_visible_run(
            run,
            readable=str(run.get("_resource_id") or "") in visible_ids,
        )
        for run in runs
    ]
    # Select display entries from the complete activity snapshot, before either
    # anonymization or response limits can hide a Resident's running entry.
    running_resident_ids = {
        str(run.get("resident_id") or "")
        for run in runs
        if run.get("status") != "queued"
    }
    avatars = {resident_id: get_avatar(resident_id) for resident_id in visible_ids if resident_id in resource_ids}

    running_runs: list[dict[str, Any]] = []
    queued_runs: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    for source in actor_visible_runs:
        run = dict(source)
        status = "queued" if run.get("status") == "queued" else "running"
        account_id = str(run.get("account_id") or "other")
        group = groups.setdefault(
            account_id,
            {
                "account_id": account_id,
                "running": 0,
                "queued": 0,
                "queued_display_count": 0,
                "running_runs": [],
                "queued_runs": [],
                "max_concurrency": account_limits.get(account_id),
            },
        )
        group[status] += 1
        if status == "queued":
            if str(run.get("_resource_id") or "") in running_resident_ids:
                continue
            group["queued_display_count"] += 1
        binding_key = str(run.pop("_sprite_binding_key", ""))
        run.pop("_resource_id", None)
        resident_id = str(run.get("resident_id") or "")
        if resident_id:
            run.update(projection_fields(avatars.get(resident_id)))
        else:
            index = int.from_bytes(hashlib.sha256(binding_key.encode("utf-8")).digest()[:8], "big") % 36
            run.update({"sprite_index": index, "sprite_rarity": SPRITE_RARITIES[index]})
        target = running_runs if status == "running" else queued_runs
        target.append(run)
        group[f"{status}_runs"].append(run)

    account_order = {account_id: index for index, account_id in enumerate(account_limits)}
    account_groups = sorted(
        groups.values(),
        key=lambda item: (
            account_order.get(str(item.get("account_id") or ""), len(account_order)),
            str(item.get("account_id") or ""),
        ),
    )
    for group in account_groups:
        group["running_runs"] = group["running_runs"][:48]
        group["queued_runs"] = group["queued_runs"][:48]

    error = "; ".join(errors[:5])
    return {
        "status": "error" if error else "ok",
        "ready": True,
        "refreshing": False,
        "error": error,
        "running": sum(
            1 for run in actor_visible_runs if run.get("status") != "queued"
        ),
        "queued": sum(
            1 for run in actor_visible_runs if run.get("status") == "queued"
        ),
        "queued_display_count": sum(group["queued_display_count"] for group in account_groups),
        "running_runs": running_runs[:48],
        "queued_runs": queued_runs[:48],
        "account_groups": account_groups,
        "max_concurrency": max_concurrency,
        "updated_at": utc_now_iso(),
    }

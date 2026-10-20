from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import contextvars
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any

from management_portal.society_service import get_society_service
from management_portal.portal_ui_state import get_portal_ui_state_store
from management_portal.resident_recommendation_backend import recommendation_chat_binding
from management_portal.resident_discovery_client import ResidentDiscoveryUnavailable


PERSONAL_AMA_UI_NAMESPACE = "lineage-workbench.personal-ama"
SIDEBAR_SNAPSHOT_CACHE_TTL_SECONDS = 15.0
logger = logging.getLogger(__name__)

_SNAPSHOT_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SNAPSHOT_INFLIGHT: dict[str, Future[dict[str, Any]]] = {}
_SNAPSHOT_CACHE_GENERATION = 0


def _lineage_sidebar_items(payload: object) -> list[dict[str, Any]]:
    source = payload if isinstance(payload, dict) else {}
    items: list[dict[str, Any]] = []
    for raw in source.get("lineages") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        node_ids = list(dict.fromkeys(
            str(value or "").strip()
            for value in item.get("lineage_node_ids") or []
            if str(value or "").strip()
        ))
        item.update({
            "created_by": str(item.get("created_by") or ""),
            "lineage_node_ids": node_ids,
            "resident_count": len(node_ids),
        })
        items.append(item)
    return items


def _resident_sidebar_item(
    raw: dict[str, Any],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    resident_id = str(raw.get("resident_id") or "").strip()
    lineage_id = str(raw.get("lineage_id") or "").strip()
    node_ids = list(lineage.get("lineage_node_ids") or [])
    leader_id = str(lineage.get("leader_resident_id") or "").strip()
    return {
        **raw,
        "lineage_id": lineage_id,
        "is_lineage_member": resident_id in set(node_ids),
        "lineage_leader_resident_id": leader_id,
        "is_lineage_leader": resident_id == leader_id,
        "created_by": str(raw.get("created_by") or ""),
        "lineage": {
            "lineage_id": lineage_id,
            "title": str(lineage.get("title") or ""),
            "project_id": str(lineage.get("project_id") or ""),
            "leader_resident_id": leader_id,
            "created_by": str(lineage.get("created_by") or ""),
        },
    }


def _append_personal_ama_presentation(
    result: dict[str, Any],
    actor: str,
) -> None:
    association = get_portal_ui_state_store().get(
        actor,
        PERSONAL_AMA_UI_NAMESPACE,
        "primary",
    )
    resident_id = str(
        ((association or {}).get("value") or {}).get("resident_id") or ""
    ).strip()
    residents = result.setdefault("residents", [])
    if (
        not resident_id
        or not isinstance(residents, list)
        or any(
            str(item.get("resident_id") or "") == resident_id
            for item in residents
            if isinstance(item, dict)
        )
    ):
        return
    service = get_society_service().resident
    identity = service.get_resident(resident_id, actor)
    state = service.get_resident_state(resident_id, actor)
    residents.append({
        **dict(identity or {}),
        "resident_state": dict((state or {}).get("state") or {}),
        "is_lineage_leader": True,
    })


# The Project and Lineage catalogs are independent reads.
_DIRECTORY_READS = ThreadPoolExecutor(max_workers=6, thread_name_prefix="sidebar-read")


def _read_concurrently(*reads: Any) -> list[Any]:
    futures = [_DIRECTORY_READS.submit(contextvars.copy_context().run, read) for read in reads]
    return [future.result() for future in futures]


def snapshot(actor: str) -> dict[str, Any]:
    """Build Project and Lineage navigation from their catalog summaries."""

    service = get_society_service()
    actor_id = str(actor or "")
    lineage_rows, principal_hierarchy = _read_concurrently(
        lambda: service.lineage.list_lineages(actor_id),
        lambda: service.lineage.list_principal_hierarchy(actor_id),
    )
    lineages = _lineage_sidebar_items(lineage_rows)
    # The collapsed Navigation shows one card per Lineage. Its title, leader
    # target and count are already present in the Lineage summary; the member
    # names are loaded by the explicit Lineage menu.
    residents = [
        _resident_sidebar_item({
            "resident_id": lineage["leader_resident_id"],
            "lineage_id": lineage["lineage_id"],
            "title": lineage.get("title") or lineage["lineage_id"],
            "created_by": str(lineage.get("created_by") or ""),
        }, lineage)
        for lineage in lineages if lineage.get("leader_resident_id")
    ]
    result = {
        "schema_version": lineage_rows.get("schema_version"),
        "principal_hierarchy": principal_hierarchy,
        "lineages": lineages,
        "residents": residents,
    }
    result.update({
        "status": "ok",
        "ready": True,
        "refreshing": False,
        "error": "",
        "updated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
    })
    return result


def shortcuts(actor: str) -> dict[str, Any]:
    """Load the two personal Chat shortcuts outside the navigation request."""

    from management_portal.execution.accounts import default_execution_accounts

    actor_id = str(actor or "")
    service = get_society_service()
    result: dict[str, Any] = {"residents": []}
    try:
        _append_personal_ama_presentation(result, actor_id)
    except (KeyError, PermissionError, ValueError):
        pass
    try:
        binding = recommendation_chat_binding(actor_id)
        identity = service.resident.get_resident(binding["resident_id"], actor_id)
        result["recommendation_resident"] = {**dict(identity), **binding}
        result["recommendation_status"] = "available"
        if not any(item.get("resident_id") == binding["resident_id"] for item in result["residents"]):
            result["residents"].append(result["recommendation_resident"])
    except (KeyError, PermissionError, ValueError, ResidentDiscoveryUnavailable) as exc:
        result["recommendation_resident"] = None
        result["recommendation_status"] = "denied" if isinstance(exc, PermissionError) else "unavailable"
        logger.warning("Resident discovery sidebar unavailable: %s: %s", type(exc).__name__, exc, exc_info=True)
    result["account_selection"] = default_execution_accounts().resident_selection("codex", "")
    return result


def invalidate_snapshot_cache() -> None:
    """Discard process-local sidebar projections after durable changes."""

    global _SNAPSHOT_CACHE_GENERATION
    with _SNAPSHOT_CACHE_LOCK:
        _SNAPSHOT_CACHE_GENERATION += 1
        _SNAPSHOT_CACHE.clear()


def cached_snapshot(actor: str) -> dict[str, Any]:
    """Coalesce concurrent cold sidebar reads without sharing service state."""

    actor_id = str(actor or "")
    now = time.monotonic()
    owns_refresh = False
    with _SNAPSHOT_CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(actor_id)
        if cached and now < cached[0]:
            return cached[1]
        refresh = _SNAPSHOT_INFLIGHT.get(actor_id)
        if refresh is None:
            refresh = Future()
            _SNAPSHOT_INFLIGHT[actor_id] = refresh
            generation = _SNAPSHOT_CACHE_GENERATION
            owns_refresh = True

    if not owns_refresh:
        return refresh.result(timeout=30.0)

    try:
        result = snapshot(actor_id)
    except BaseException as exc:
        with _SNAPSHOT_CACHE_LOCK:
            if _SNAPSHOT_INFLIGHT.get(actor_id) is refresh:
                _SNAPSHOT_INFLIGHT.pop(actor_id, None)
        refresh.set_exception(exc)
        raise

    with _SNAPSHOT_CACHE_LOCK:
        if _SNAPSHOT_INFLIGHT.get(actor_id) is refresh:
            _SNAPSHOT_INFLIGHT.pop(actor_id, None)
            if generation == _SNAPSHOT_CACHE_GENERATION:
                _SNAPSHOT_CACHE[actor_id] = (
                    time.monotonic() + SIDEBAR_SNAPSHOT_CACHE_TTL_SECONDS,
                    result,
                )
    refresh.set_result(result)
    return result



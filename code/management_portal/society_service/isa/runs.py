"""Lineage run instructions (decision_68215b3a825b)."""
from __future__ import annotations

import time
from management_common.service_shutdown import wait_for_shutdown
from typing import Any, Iterator

from management_portal.hatg.declarations import cursor_offset, offset_cursor
from management_portal.hatg.kernel import Invocation

from .common import domain


STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0


def _emit(inv: Invocation, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if inv.argument("event_source_id"):
        payload["event_source_id"] = inv.argument("event_source_id")
    if inv.argument("wire_id"):
        payload["wire_id"] = inv.argument("wire_id")
    for name in ("prompt", "content_parts"):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    payload.update({key: value for key, value in extra.items() if value is not None})
    return domain().emit_program_manual_event(inv.argument("lineage_id"), payload, inv.caller)


def _event_result(result: dict[str, Any]) -> dict[str, Any]:
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    delivery = {
        key: result[key]
        for key in ("matched_count", "consumed_count", "duplicate", "results")
        if key in result
    }
    return {
        "event_id": str(event.get("id") or ""),
        "trigger_path_id": str(result.get("trigger_path_id") or ""),
        "delivery": delivery,
    }


def trigger_event(inv: Invocation) -> dict[str, Any]:
    return _event_result(_emit(
        inv,
        event_name=inv.argument("event_name"),
        subject_resident_id=inv.argument("subject_resident_id"),
        payload=inv.argument("payload"),
    ))




def resolve_effect_activation(inv: Invocation) -> dict[str, Any]:
    from ..effect_disposition import resolve

    return {"activation": resolve(inv.argument("lineage_id"), inv.argument("activation_key"),
                                  inv.argument("status"), inv.caller, note=inv.argument("note") or "")}


def resolve_trigger_path(inv: Invocation) -> dict[str, Any]:
    from ..effect_disposition import resolve_path

    return {"path": resolve_path(inv.argument("lineage_id"), inv.argument("trigger_path_id"), inv.caller,
                                 note=inv.argument("note") or "")}


def get_component_runtime(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import component_runtime

    return {
        "components": component_runtime.views(
            inv.argument("lineage_id"), inv.caller, component_id=inv.argument("component_id") or "",
        )
    }


def pause_component(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import component_runtime

    return {"runtime": component_runtime.set_paused(
        inv.argument("lineage_id"), inv.argument("component_id"), bool(inv.argument("paused")), inv.caller,
    )}


def reset_component_runtime(inv: Invocation) -> dict[str, Any]:
    if inv.argument("wire_id"):
        from .. import wire_delivery_budget

        runtime = wire_delivery_budget.reset(inv.argument("lineage_id"), inv.argument("wire_id"), inv.caller)
    else:
        from management_portal.society_service import component_runtime

        runtime = component_runtime.reset(inv.argument("lineage_id"), inv.argument("component_id"), inv.caller)
    return {"runtime": runtime, "generation": int(runtime.get("generation") or 0)}


def _observe(inv: Invocation, **fields: Any) -> dict[str, Any]:
    payload = {key: value for key, value in fields.items() if value is not None}
    return domain().observe_trigger_path(inv.argument("lineage_id"), payload, inv.caller)


def _next_cursor(page: dict[str, Any]) -> dict[str, Any]:
    """A page in the ISA's paging names: ``next_offset`` becomes ``next_cursor``, given only while a page follows."""
    page = dict(page)
    following = offset_cursor(page.pop("next_offset", None))
    if following:
        page["next_cursor"] = following
    return page


def list_trigger_paths(inv: Invocation) -> dict[str, Any]:
    return _next_cursor(_observe(inv, offset=cursor_offset(inv.argument("cursor")), limit=inv.argument("limit"),
                                 resident_id=inv.argument("resident_id")))


def get_trigger_path(inv: Invocation) -> dict[str, Any]:
    return _observe(inv, trigger_path_id=inv.argument("trigger_path_id"), offset=inv.argument("offset"),
                    limit=inv.argument("limit"), artifact_activation_key=inv.argument("artifact_activation_key"))



def watch_trigger_path(inv: Invocation) -> Iterator[list[dict[str, Any]]]:
    cursor = int(inv.argument("after_cursor") or 0)
    last_yield = time.monotonic()
    while True:
        result = _observe(inv, trigger_path_id=inv.argument("trigger_path_id"), limit=200)
        path = result["path"]
        revision = int(path["cursor"])
        if revision > cursor:
            cursor = revision
            last_yield = time.monotonic()
            yield [{"cursor": cursor, "change": {"type": "trigger_path.updated", **result}}]
        elif time.monotonic() - last_yield >= STREAM_HEARTBEAT_SECONDS:
            last_yield = time.monotonic()
            yield []
        if path["status"] in {"completed", "failed", "cancelled"}:
            return
        if wait_for_shutdown(STREAM_POLL_SECONDS):
            return


def list_pending_deliveries(inv: Invocation) -> dict[str, Any]:
    result = domain().get_pending_deliveries(
        inv.argument("lineage_id"), {"wire_id": inv.argument("wire_id")}, inv.caller,
    )
    return {**result, "deliveries": result.get("deliveries") or []}


def update_pending_delivery(inv: Invocation) -> dict[str, Any]:
    payload = {
        "wire_id": inv.argument("wire_id"),
        "delivery_id": inv.argument("delivery_id"),
        "prompt": inv.argument("prompt") or "",
    }
    result = domain().update_pending_delivery(inv.argument("lineage_id"), payload, inv.caller)
    return {"deliveries": result.get("deliveries") or []}


def delete_pending_delivery(inv: Invocation) -> dict[str, Any]:
    payload = {
        "wire_id": inv.argument("wire_id"),
        "delivery_id": inv.argument("delivery_id"),
    }
    result = domain().delete_pending_delivery(inv.argument("lineage_id"), payload, inv.caller)
    return {"deliveries": result.get("deliveries") or []}


HANDLERS = {
    "ResolveEffectActivation": resolve_effect_activation,
    "ResolveTriggerPath": resolve_trigger_path,
    "TriggerEvent": trigger_event,
    "GetComponentRuntime": get_component_runtime,
    "PauseComponent": pause_component,
    "ResetComponentRuntime": reset_component_runtime,
    "ListTriggerPaths": list_trigger_paths,
    "GetTriggerPath": get_trigger_path,
    "ListPendingDeliveries": list_pending_deliveries,
    "UpdatePendingDelivery": update_pending_delivery,
    "DeletePendingDelivery": delete_pending_delivery,
}
STREAMS = {"WatchTriggerPath": watch_trigger_path}

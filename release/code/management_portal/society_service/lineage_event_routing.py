"""Select Event subscribers in wire order and apply unavailable policies.

The caller holds the Lineage lock. Availability is supplied by the caller
because it reads Resident state outside the Program runtime's authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import lineage_event_claims, lineage_event_dispatch_rules, lineage_primitives


@dataclass(frozen=True)
class RouteSelection:
    available_routes: list[dict[str, Any]]
    unavailable_results: list[dict[str, Any]]
    handled_unavailable_count: int
    duplicate_unavailable_count: int


def select_event_routes(
    lineage_id: str,
    event_source_id: str,
    routes: list[dict[str, Any]],
    event: lineage_primitives.LineageEvent,
    cursor: str,
    wire_runtime: dict[str, Any],
    *,
    bypass_unavailable_policy: bool,
    can_consume_target: Callable[[dict[str, Any]], tuple[bool, str]],
) -> RouteSelection:
    """Keep available routes; defer the others in their original order."""
    available_routes: list[dict[str, Any]] = []
    unavailable_results: list[dict[str, Any]] = []
    handled_unavailable_count = 0
    duplicate_unavailable_count = 0

    for route in routes:
        if not isinstance(route, dict) or not str(route.get("wire_id") or ""):
            continue
        wire_id = str(route.get("wire_id") or "")
        # A parallel wire takes a new occurrence while earlier ones are still delivered.
        parallel = bool((route.get("config") or {}).get("parallel"))
        if not parallel and lineage_event_dispatch_rules.backlog_blocks_occurrence(
            wire_runtime.get(wire_id) or {},
            cursor=cursor,
            event_source_id=event_source_id,
        ):
            available, reason = False, "wire_backlog"
        else:
            available, reason = can_consume_target(route)
        if available:
            available_routes.append(route)
            continue
        result = (
            {
                "status": "unavailable",
                "wire_id": wire_id,
                "target_id": str(route.get("target_id") or ""),
                "target_kind": str(route.get("target_kind") or ""),
                "reason": reason,
                "event_id": event.id,
            }
            if bypass_unavailable_policy
            else lineage_event_claims.defer_program_event_route(
                lineage_id,
                event_source_id,
                route,
                event,
                cursor,
                reason=reason,
            )
        )
        unavailable_results.append(result)
        if result.get("status") == "duplicate":
            duplicate_unavailable_count += 1
        elif result.get("status") != "unavailable":
            handled_unavailable_count += 1
    return RouteSelection(
        available_routes,
        unavailable_results,
        handled_unavailable_count,
        duplicate_unavailable_count,
    )

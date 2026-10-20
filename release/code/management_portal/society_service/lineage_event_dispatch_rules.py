"""Event subscriber route rules.

The dispatcher owns claims, locks, sends and persistence; this module only
chooses the route configuration and evaluates runtime availability facts.
"""
from __future__ import annotations

from typing import Any

from . import lineage_primitives


def prepare_subscriber_routes(
    event_source: dict[str, Any],
    selected_wire_ids: set[str] | None,
    invocation_arguments: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    selected_wire_id_set = {
        str(item) for item in selected_wire_ids or set() if str(item)
    }
    subscriber_routes = [
        dict(route)
        for route in event_source.get("subscriber_routes") or []
        if (
            isinstance(route, dict)
            and str(route.get("wire_id") or "")
            and (
                not selected_wire_id_set
                or str(route.get("wire_id") or "")
                in selected_wire_id_set
            )
        )
    ]
    for route in subscriber_routes:
        overrides = (invocation_arguments or {}).get(
            str(route.get("wire_id") or ""),
        )
        if overrides:
            # Queue edits are runtime values, not authored literal mappings.
            route["argument_overrides"] = lineage_primitives.normalize_invocation_arguments(overrides)
    if (
        str(event_source.get("event_type") or "") == "time.reached"
        and str(event_source.get("schedule_mode") or "") == "recurring"
    ):
        overlap_policy = str(
            event_source.get("overlap_policy") or "drop"
        )
        for route in subscriber_routes:
            route_config = dict(
                route.get("config")
                if isinstance(route.get("config"), dict)
                else {}
            )
            if overlap_policy == "queue_one":
                route_config.update(
                    {"unavailable_policy": "queue", "queue_limit": 1}
                )
            elif overlap_policy == "latest":
                route_config["unavailable_policy"] = "latest"
                route_config.pop("queue_limit", None)
            else:
                route_config["unavailable_policy"] = "drop"
                route_config.pop("queue_limit", None)
            route["config"] = route_config
    return subscriber_routes


def backlog_blocks_occurrence(
    wire_state: dict[str, Any],
    *,
    cursor: str,
    event_source_id: str,
) -> bool:
    """A new occurrence must not overtake an older one on the same wire."""
    return int(wire_state.get("pending_count") or 0) > 0 and not (
        str(wire_state.get("oldest_cursor") or "") == cursor
        and str(wire_state.get("oldest_event_source_id") or "") == event_source_id
    )


# Events that start background work; each such run gets a fresh context
# unless its wire keeps the Resident's session (config.context_policy).
BACKGROUND_EVENT_TYPES = frozenset({"time.reached", "file.changed"})


def _program_event_delivery_profile(
    event: lineage_primitives.LineageEvent,
    route: dict[str, Any],
) -> dict[str, Any]:
    """How the target Resident runs this delivery.

    Every delivery is a model turn of the Resident.  Time and file work runs
    in a fresh context instead of the Resident's session unless
    ``config.context_policy = "session"`` keeps the session.
    """

    config = route.get("config") if isinstance(route.get("config"), dict) else {}
    isolated = (
        event.type in BACKGROUND_EVENT_TYPES
        and str(config.get("context_policy") or "") != "session"
    )
    return {"isolated": True} if isolated else {}

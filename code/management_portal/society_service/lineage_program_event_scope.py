"""Which Residents an Event Source observes: its Watch subjects, or every Resident of its
Lineage or of Portal, and the completion subscriptions that follow from them.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import lineage_primitive_definitions
from .lineage_program_vocabulary import authoring_component_config


# An Event Source of these types may watch several Residents; any one of
# them fires it, and each Event names the Resident it is about.
EVENT_SOURCE_MULTI_RESIDENT_TYPES = set(
    lineage_primitive_definitions.event_types_watching_many()
)


def event_source_subject_ids(source: object) -> tuple[str, ...]:
    """The Residents a compiled Event Source watches: none, one or several."""

    if not isinstance(source, dict):
        return ()
    many = source.get("subject_resident_ids")
    if isinstance(many, (list, tuple)) and many:
        return tuple(dict.fromkeys(str(item) for item in many if str(item)))
    single = str(source.get("subject_resident_id") or "")
    return (single,) if single else ()


def watches_many_residents(source: object) -> bool:
    """Whether a compiled Event Source watches more than one Resident."""

    return len(event_source_subject_ids(source)) > 1


# A Resident finishing, failing or cancelling work may be observed for the
# Residents its Watch wires name ("watched"), every Resident of the Lineage
# ("lineage") or every Resident of Portal ("system").  A scoped source needs
# no Watch wire and covers Residents added later; it never fires for work it
# delivered itself, directly or through an Effect.
SCOPED_RESIDENT_EVENT_TYPES = frozenset({
    "resident.work_finished",
    "resident.work_failed",
    "resident.work_cancelled",
})
RESIDENT_SCOPES = ("watched", "lineage", "system")
# Event types whose Watch wire may name a Resident.
RESIDENT_SUBJECT_EVENT_TYPES = frozenset({
    "resident.work_finished",
    "resident.work_started",
    "resident.work_failed",
    "resident.work_cancelled",
    "resident.state_changed",
    "resident.context_pressure_exceeded",
    "resident.work_duration_exceeded",
    "resident.queue_pressure_exceeded",
    "resident.message_received",
    "file.changed",
})


def event_source_resident_scope(source: object) -> str:
    """``watched``, ``lineage`` or ``system`` for a compiled Event Source."""

    if not isinstance(source, dict):
        return "watched"
    if str(source.get("event_type") or "") not in SCOPED_RESIDENT_EVENT_TYPES:
        return "watched"
    scope = str(source.get("resident_scope") or "").strip().lower()
    return scope if scope in {"lineage", "system"} else "watched"


def _component_resident_scope(component: object) -> str:
    """The Resident scope an authored Event Source component declares."""

    if not isinstance(component, dict) or component.get("kind") != "event_source":
        return "watched"
    config = authoring_component_config(component)
    return event_source_resident_scope({
        "event_type": str(config.get("event_type") or "").strip().lower(),
        "resident_scope": config.get("resident_scope"),
    })


def event_source_covers(source: object, resident_id: str, member_ids: Iterable[str] = ()) -> bool:
    """Whether a compiled Event Source observes ``resident_id``.

    ``member_ids`` are the Residents of the source's own Lineage.
    """

    scope = event_source_resident_scope(source)
    if scope == "system":
        return bool(resident_id)
    if scope == "lineage":
        return bool(resident_id) and resident_id in set(member_ids)
    return resident_id in event_source_subject_ids(source)


def event_source_residents(source: object, member_ids: Iterable[str]) -> tuple[str, ...]:
    """The Residents of the source's Lineage it observes, or its watched Residents.

    A scoped source observes every member; Residents outside the Lineage
    reach a system-scope source only as their terminal facts arrive.
    """

    if event_source_resident_scope(source) == "watched":
        return event_source_subject_ids(source)
    return tuple(sorted({str(item) for item in member_ids if str(item)}))


def resident_completion_subscriptions(
    plan: dict[str, Any], member_ids: Iterable[str],
) -> list[tuple[str, tuple[str, ...], bool, list[str]]]:
    """Each ``resident.work_finished`` source as (id, observed Residents, per-Resident cursor, subscriber wire IDs).

    A source watching several Residents, or a scoped one, keeps one cursor
    per Resident.
    """

    members = tuple(member_ids)
    subscriptions = []
    for event_source_id, source in sorted((plan.get("event_sources") or {}).items()):
        if not isinstance(source, dict) or str(source.get("event_type") or "") != "resident.work_finished":
            continue
        watched = event_source_residents(source, members)
        per_resident = len(watched) > 1 or event_source_resident_scope(source) != "watched"
        wire_ids = [
            str(route.get("wire_id") or "")
            for route in source.get("subscriber_routes") or []
            if isinstance(route, dict) and str(route.get("wire_id") or "")
        ]
        subscriptions.append((str(event_source_id), watched, per_resident, wire_ids))
    return subscriptions

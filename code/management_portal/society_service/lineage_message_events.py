"""Recognize ``resident.message_received``: one Event per input a watched Resident receives.

The Resident Conversation keeps an ordered event log with a cursor.  A
``resident.message_received`` source remembers the last cursor it handled
as its scalar runtime baseline.  Each pass reads the log after that cursor and
turns the first ``queued`` or ``running`` input state of every message into one
Event.  The Event's initiator is the message's sender, so Effects activated by
it act with the sender's own permissions (decision_44cf587c1721).

Arrivals are also pushed: accepting a message at a watched Resident hands
that one message to the arrival handler, which dispatches its Event at once on
its own worker (``dispatch_arrival``).  A Family Router therefore starts when
the message arrives, and several messages route in parallel.  The pass still
reads the log as a backstop; it uses the same cursor key, finds a pushed Event
already fired and only moves its baseline.

``relay_depth`` counts how many Family facades the message already passed:
a message a Family Router delivered carries ``business_ref`` =
``<facade resident>/message/<message id>`` of the message it was routed from.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Iterable, Mapping

from . import lineage_primitives, lineage_program, lineage_program_runtime, resident_conversation_stream

MESSAGE_RECEIVED_EVENT = "resident.message_received"
ARRIVAL_STATES = frozenset({"queued", "running"})
# One pass reads at most this many log entries per source; the next pass continues.
MAX_EVENTS_PER_PASS = 200
MAX_RELAY_LOOKUP = 8
RELAY_REF = re.compile(r"^(resident_[0-9a-f]{12})/message/([^/\s]+)$")
_CURSOR_PREFIX = "conversation_cursor:"
_LATEST_CURSOR = 10**15
_ARRIVAL_HANDLER: Callable[[str, str, str], None] | None = None


def set_arrival_handler(handler: Callable[[str, str, str], None] | None) -> None:
    """Install the process's arrival dispatcher (the Lineage coordinator)."""

    global _ARRIVAL_HANDLER
    _ARRIVAL_HANDLER = handler


def message_locator(subject_id: str) -> str:
    return "resident_message_locator_" + hashlib.sha256(subject_id.encode("utf-8")).hexdigest()


def cursor_value(cursor: int) -> str:
    return f"{_CURSOR_PREFIX}{int(cursor)}"


def cursor_signature(cursor: int) -> str:
    return "sha256:" + hashlib.sha256(cursor_value(cursor).encode("utf-8")).hexdigest()


def parse_cursor_value(value: object) -> int | None:
    text = str(value or "")
    if not text.startswith(_CURSOR_PREFIX):
        return None
    try:
        return max(0, int(text[len(_CURSOR_PREFIX):]))
    except ValueError:
        return None


def message_arrivals(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The first arrival state of each message in one ordered slice of the log."""

    arrivals: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if str(event.get("event_type") or "") != resident_conversation_stream.INPUT_STATE_CHANGED:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        state = payload.get("input_state") if isinstance(payload.get("input_state"), Mapping) else {}
        message_id = str(state.get("message_id") or "").strip()
        if not message_id or message_id in seen or str(state.get("state") or "") not in ARRIVAL_STATES:
            continue
        seen.add(message_id)
        arrivals.append({
            "message_id": message_id,
            "cursor": int(event.get("cursor") or 0),
            "occurred_at": str(event.get("occurred_at") or ""),
        })
    return arrivals


def relay_depth(
    resident_id: str,
    message_id: str,
    business_ref_of: Callable[[str, str], str],
    *,
    limit: int = MAX_RELAY_LOOKUP,
) -> int:
    """How many facade messages lead to this one through ``business_ref`` links."""

    depth = 0
    current = (resident_id, message_id)
    visited = {current}
    while depth < limit:
        match = RELAY_REF.match(str(business_ref_of(*current) or "").strip())
        if not match:
            break
        current = (match.group(1), match.group(2))
        if current in visited:
            break
        visited.add(current)
        depth += 1
    return depth


def message_received_sources(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(source_id): source
        for source_id, source in (plan.get("event_sources") or {}).items()
        if isinstance(source, dict)
        and str(source.get("event_type") or "") == MESSAGE_RECEIVED_EVENT
        and lineage_program.event_source_subject_ids(source)
    }


def _sender_identity(actor_id: str, author_id: str) -> dict[str, Any] | None:
    from management_common.principal import OperationIdentity

    if not author_id:
        return None
    try:
        return OperationIdentity.for_actor(actor_id, initiated_by=author_id).to_dict()
    except ValueError:
        return None


def _facade_record(resident_id: str) -> dict[str, Any] | None:
    from . import family_facade
    from . import resident_record_domain

    record = resident_record_domain.find_resident(resident_id)
    return record if family_facade.is_facade(record) else None


def _facade_message_waiting(repository: Any, facade: Mapping[str, Any], message_id: str) -> bool:
    """A facade message still waits for the Router: not relayed and not finished."""

    from . import family_facade, resident_held_infos

    if message_id in family_facade.relay_links(facade):
        return False
    if resident_held_infos.is_info(facade, message_id):
        # An info goes to the steward, never to the Router.
        return False
    try:
        state = repository.input_state(str(facade.get("resident_id") or ""), message_id)
    except KeyError:
        return True
    return str(state.get("state") or "") in ARRIVAL_STATES


def pushed_source(source: Mapping[str, Any]) -> bool:
    routes = [route for route in source.get("subscriber_routes") or [] if isinstance(route, Mapping)]
    return bool(routes) and all(bool((route.get("config") or {}).get("parallel")) for route in routes)


def arrival_details(
    repository: Any, subject_id: str, message_id: str, actor_id: str, facade: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Sender, relay depth and identity of one arrival; None when a facade no longer waits for it."""

    occurrence_id = message_id
    if facade:
        from . import family_facade, family_recovery

        message_id = family_recovery.arrival(facade, message_id)
        if not message_id:
            return None

        if ((not family_facade.family_members(facade) and not family_facade.family_steward_id(facade))
                or not _facade_message_waiting(repository, facade, message_id)):
            return None
    message = repository.message(subject_id, message_id) or {}
    author_id = str(message.get("author_id") or "")
    details = {
        "message_id": message_id,
        **({"occurrence_id": occurrence_id} if occurrence_id != message_id else {}),
        "author_id": author_id,
        "relay_depth": relay_depth(subject_id, message_id, repository.message_business_ref),
        "identity": _sender_identity(actor_id, author_id),
    }
    if facade:
        # A request quoting a member's message goes straight to that member (decision_44cf587c1721).
        from .family_facade import quoted_member

        details["quoted_member_id"] = quoted_member(facade, message)
    return details


def arrival_cursor(subject_id: str, message_id: str) -> str:
    """The occurrence key of one arrival; the push and the pass share it."""

    return f"message-received:{subject_id}:{message_id}"


def arrival_event(event_source_id: str, subject_id: str, arrival: Mapping[str, Any]) -> lineage_primitives.LineageEvent:
    return lineage_primitives.make_event(
        MESSAGE_RECEIVED_EVENT,
        subject_resident_id=subject_id,
        occurred_at=str(arrival.get("occurred_at") or ""),
        payload={
            "resident_id": subject_id,
            "message_id": arrival["message_id"],
            "author_id": arrival["author_id"],
            "relay_depth": arrival["relay_depth"],
            "quoted_member_id": arrival.get("quoted_member_id") or "",
        },
        correlation=f"{event_source_id}:{subject_id}:{arrival.get('occurrence_id') or arrival['message_id']}",
        identity=arrival["identity"],
    )


def dispatch_arrival(
    plan: Mapping[str, Any],
    actor_id: str,
    subject_id: str,
    message_id: str,
    dispatch: Callable[[str, lineage_primitives.LineageEvent, str, Mapping[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dispatch ``resident.message_received`` for one accepted message to the sources that take it at once.

    Only a source whose every route is parallel is pushed (a Family Router):
    an ordered route keeps taking its Events one after another from the pass.
    """

    sources = {
        source_id: source for source_id, source in message_received_sources(plan).items()
        if subject_id in lineage_program.event_source_subject_ids(source) and pushed_source(source)
    }
    if not sources or not message_id:
        return []
    repository = resident_conversation_stream.get_repository()
    try:
        state = repository.input_state(subject_id, message_id)
    except KeyError:
        # Not accepted (yet); the pass finds it in the log.
        return []
    details = arrival_details(repository, subject_id, message_id, actor_id, _facade_record(subject_id))
    if details is None:
        return []
    arrival = {**details, "occurred_at": str(state.get("updated_at") or "")}
    return [
        dispatch(source_id, arrival_event(source_id, subject_id, arrival), arrival_cursor(subject_id, message_id), source)
        for source_id, source in sorted(sources.items())
    ]


def prefetch_message_arrivals(
    lineage_id: str,
    sources: Mapping[str, Mapping[str, Any]],
    actor_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Read each watched Conversation outside the Lineage lock.

    ``base_signature`` is the runtime baseline the reads started from; the
    locked pass only uses a prefetch whose baseline is still current.  Results
    are keyed by source and watched Resident; the Resident part is empty for a
    source that watches one.
    """

    if not sources:
        return {}
    runtime_sources = lineage_program_runtime.read_runtime(lineage_id).get("event_sources") or {}
    repository = resident_conversation_stream.get_repository()
    result: dict[tuple[str, str], dict[str, Any]] = {}
    watched = [
        (source_id, subject_id, subject_id if lineage_program.watches_many_residents(source) else "")
        for source_id, source in sources.items()
        for subject_id in lineage_program.event_source_subject_ids(source)
    ]
    for source_id, subject_id, scope in watched:
        locator = message_locator(subject_id)
        stored = lineage_program_runtime.observation_state(runtime_sources, source_id, scope)
        base_cursor = (
            parse_cursor_value(stored.get("state_value"))
            if str(stored.get("state_locator") or "") == locator
            else None
        )
        facade = _facade_record(subject_id)
        if facade:
            from . import family_facade

            # Organization requests can reach the steward with an empty roster.
            # Retain the cursor only when no candidate role exists at all.
            if not family_facade.family_members(facade) and not family_facade.family_steward_id(facade):
                continue
        try:
            if base_cursor is None:
                # A Family facade's waiting messages all belong to its Router,
                # including those that arrived before the Router first looked.
                page = {"cursor": 0} if facade else repository.events_after(subject_id, _LATEST_CURSOR, limit=1)
                result[(source_id, scope)] = {
                    "subject_id": subject_id,
                    "locator": locator,
                    "base_signature": None,
                    "base_cursor": None,
                    "cursor": int(page.get("cursor") or 0),
                    "arrivals": [],
                }
                continue
            page = repository.events_after(subject_id, base_cursor, limit=MAX_EVENTS_PER_PASS)
        except KeyError:
            # The Resident has no Conversation yet; nothing has arrived.
            continue
        events = list(page.get("events") or [])
        next_cursor = int(events[-1]["cursor"]) if events else max(base_cursor, int(page.get("cursor") or 0))
        arrivals = []
        for arrival in message_arrivals(events):
            details = arrival_details(repository, subject_id, arrival["message_id"], actor_id, facade)
            if details is not None:
                arrivals.append({**arrival, **details})
        result[(source_id, scope)] = {
            "subject_id": subject_id,
            "locator": locator,
            "base_signature": str(stored.get("state_signature") or ""),
            "base_cursor": base_cursor,
            "cursor": next_cursor,
            "arrivals": arrivals,
        }
    return result


def watched_by_message_sources(definition: Mapping[str, Any], resident_id: str) -> bool:
    """Whether a ``resident.message_received`` source in this Lineage watches the Resident."""

    program = definition.get("program") if isinstance(definition.get("program"), Mapping) else {}
    sources = {
        str(item.get("id") or "")
        for item in program.get("components") or []
        if isinstance(item, Mapping) and item.get("kind") == "event_source"
        and str((item.get("config") or {}).get("event_type") or "") == MESSAGE_RECEIVED_EVENT
    }
    if not sources:
        return False
    return any(
        isinstance(wire, Mapping) and wire.get("kind") == "watch"
        and str(wire.get("target") or "") == resident_id and str(wire.get("source") or "") in sources
        for wire in program.get("wires") or []
    )


def wake_watchers(record: Mapping[str, Any], message_id: str = "") -> bool:
    """Produce the Event now when a message_received source watches the Resident.

    The arrival handler dispatches this one message on its own worker; without
    a handler in this process the Lineage is reconciled now instead.
    """

    from . import resident_lineage
    from .changes import lineage_changed
    from .state_store import get_lineage_data_store

    try:
        lineage_id = resident_lineage.lineage_id(record)
        resident_id = resident_lineage.resident_id(record)
    except ValueError:
        return False
    if not lineage_id or not resident_id:
        return False
    try:
        definition = get_lineage_data_store().lineage_definition(lineage_id)
    except Exception:  # noqa: BLE001 - a missed wake only delays the Event until the next pass
        return False
    if not watched_by_message_sources(definition, resident_id):
        return False
    handler = _ARRIVAL_HANDLER
    if handler is not None and message_id:
        handler(lineage_id, resident_id, str(message_id))
    else:
        lineage_changed(lineage_id, runtime=True)
    return True

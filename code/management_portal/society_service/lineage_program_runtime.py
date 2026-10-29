from __future__ import annotations

import hashlib
import re
import secrets
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from management_common.database_time import iso_time
from management_common.json_columns import json_value
from management_common.limit_contract import (
    limit_reached,
    limit_remaining,
    normalize_optional_limit,
)
from management_portal.society_service import lineage_primitives, lineage_recurring_schedule, trigger_paths
from management_portal.society_service.state_store import get_lineage_data_store
from . import program_work_storage


RUNTIME_SCHEMA = "tfi.hatg-program-runtime.v4"
MAX_EVENT_CURSORS = 256
MAX_EVENT_FIRE_CURSORS = 256
# An Event Source watching several Residents keeps one observation per Resident.
MAX_WATCHED_RESIDENTS = 200
MAX_PENDING_EVENTS_PER_WIRE = 100
TERMINAL_INVOCATION_STATUSES = frozenset({"completed", "failed", "cancelled"})
PENDING_EVENT_DISPATCH_LEASE_SECONDS = 360
# Every stored runtime object is normalized when it is written: ProgramWork
# normalizes each object it loads and flushes. Reads return the stored objects
# as they are once lineage_meta records that this rule wrote all of them.
# A change to _normalize or anything it calls must add 1 here. The Release
# SocietyService then renormalizes every stored object once at its start
# (migrate_stored_runtime), and reads normalize again until that finishes.
# test_lineage_program_runtime_normalization pins the rule's source to this number.
RUNTIME_NORMALIZATION = 4
_NORMALIZATION_KEY = "program_runtime_normalization"
_TRUST_SECONDS = 60.0
_trust: tuple[float, bool] = (float("-inf"), False)


def retry_delay_seconds(attempt_count: int) -> int:
    """Wait before retrying a failed delivery: 5 s, doubling to at most 60 s."""
    return min(60, 5 * 2 ** min(4, max(1, attempt_count) - 1))


def next_retry_seconds(lineage_id: str) -> int:
    """Seconds until the earliest waiting retry of this Lineage's queued Events; 0 when none waits."""
    now = datetime.now(timezone.utc)
    due = [
        moment for wire in (read_runtime(lineage_id).get("event_wires") or {}).values()
        for item in wire.get("pending_events") or [] if not item.get("recovery_blocked")
        for moment in [lineage_recurring_schedule.parse_utc(item.get("retry_at"))]
        if moment is not None and moment > now
    ]
    return max(1, int((min(due) - now).total_seconds() + 0.999)) if due else 0


def _normalize_pending_delivery(raw: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(raw)
    prompt = item.pop("prompt", None)
    packet = item.pop("packet", None)
    # A packet owns its content budgets, including text inside carried facts.
    # Delivery metadata must not impose a second, smaller budget on that content.
    result = lineage_primitives.normalize_argument_object(item)
    if "prompt" in raw:
        result["prompt"] = lineage_primitives.normalize_invocation_prompt(prompt)
    if packet:
        fields = {"schema", "key", "message", "events", "priority", "parent_packet_ids"}
        content = lineage_primitives.normalize_packet({key: value for key, value in packet.items() if key in fields})
        metadata = lineage_primitives.normalize_argument_object({key: value for key, value in packet.items() if key not in fields})
        result["packet"] = {**metadata, **content.to_dict()}
    return result


def empty_runtime() -> dict[str, Any]:
    return {
        "schema": RUNTIME_SCHEMA,
        "event_sources": {},
        "event_wires": {},
        # Expired deliveries waiting for their expiry notice; each is
        # recorded when it expires.
        "expired_deliveries": [],
        # Invocations whose input has not settled. Every invocation and its
        # outcome is recorded in trigger_paths.
        # Runtime owned by any stateful Program participant. Role Residents and
        # Packet Operators share this map so a future looping Operator does
        # not need another persistence subsystem.
        "operators": {},
        # How the Resident runs each Event delivery: in a fresh context,
        # and for a task that only runs one command, without a model.  Keyed
        # by the delivered message id; written before the delivery.
        "delivery_profiles": {},
    }


_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f]")


def _item_id(value: object, *, field: str) -> str:
    # Every runtime read checks each stored ID (cursors included), so this stays in C.
    item_id = str(value or "").strip()
    if (
        not item_id
        or len(item_id) > 240
        or _CONTROL_CHARACTER.search(item_id)
    ):
        raise ValueError(f"{field} is invalid")
    return item_id


def _lenient_count(value: object) -> int:
    """A stored counter; anything that is not a number counts as 0."""

    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        number = 0
    return max(0, number)


def _optional_id(value: object, *, field: str) -> str:
    """A stored ID that may be absent: "" stays "", anything else must be valid."""

    text = str(value or "").strip()
    return _item_id(text, field=field) if text else ""


def _unique_ids(values: object, *, field: str) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        item_id = _item_id(raw, field=field)
        if item_id not in result:
            result.append(item_id)
    return result


def _normalized_observation(source: dict[str, Any]) -> dict[str, Any]:
    """The live observation one Event Source (or one watched Resident of it) keeps."""

    result: dict[str, Any] = {}
    state_locator = str(source.get("state_locator") or "").strip()
    state_signature = str(source.get("state_signature") or "").strip()
    state_value = str(source.get("state_value") or "").strip()
    if state_locator and state_signature and state_value:
        result["state_locator"] = _item_id(state_locator, field="state locator")
        result["state_signature"] = _item_id(state_signature, field="state signature")
        result["state_value"] = _item_id(state_value, field="state value")
        result["state_generation"] = _lenient_count(source.get("state_generation"))
        if source.get("state_epoch"):
            result["state_epoch"] = _item_id(source["state_epoch"], field="observation epoch")
    completion_locator = str(source.get("completion_locator") or "").strip()
    if completion_locator:
        result["completion_locator"] = _item_id(completion_locator, field="Resident completion locator")
        result["completion_message_id"] = _optional_id(
            source.get("completion_message_id"), field="Resident completion message id",
        )
        completion_at = _optional_id(source.get("completion_at"), field="Resident completion timestamp")
        if completion_at:
            result["completion_at"] = completion_at
        result["completion_generation"] = _lenient_count(source.get("completion_generation"))
    return result


def _fire_budget_state(source: dict[str, Any]) -> dict[str, Any]:
    """Fired cursors and counters, kept once the source has fired or was reset."""

    if not any(
        key in source
        for key in ("fired_cursors", "fire_count", "fire_generation", "last_fired_at")
    ):
        return {}
    result: dict[str, Any] = {
        "fired_cursors": _unique_ids(source.get("fired_cursors"), field="Event fire cursor")[
            -MAX_EVENT_FIRE_CURSORS:
        ],
        "fire_count": _lenient_count(source.get("fire_count")),
        "fire_generation": _lenient_count(source.get("fire_generation")),
    }
    last_fired_at = _optional_id(source.get("last_fired_at"), field="Event last_fired_at")
    if last_fired_at:
        result["last_fired_at"] = last_fired_at
    return result


def _last_delivery_state(last_delivery: dict[str, Any]) -> dict[str, Any]:
    """Counts of the source's latest fan-out, by delivery status."""

    statuses = last_delivery.get("statuses")
    normalized_statuses: dict[str, int] = {}
    if isinstance(statuses, dict):
        for raw_status, raw_count in list(statuses.items())[:24]:
            status = _item_id(raw_status, field="delivery status")
            count = _lenient_count(raw_count)
            if count:
                normalized_statuses[status] = count
    result: dict[str, Any] = {
        "at": str(last_delivery.get("at") or ""),
        "total_count": max(0, int(last_delivery.get("total_count") or 0)),
        "delivered_count": max(0, int(last_delivery.get("delivered_count") or 0)),
        "queued_count": max(0, int(last_delivery.get("queued_count") or 0)),
        "dropped_count": max(0, int(last_delivery.get("dropped_count") or 0)),
        "failed_count": max(0, int(last_delivery.get("failed_count") or 0)),
        "statuses": normalized_statuses,
    }
    for identity_field in ("event_id", "trigger_path_id"):
        identity = _optional_id(last_delivery.get(identity_field), field=f"Event delivery {identity_field}")
        if identity:
            result[identity_field] = identity
    return result


def _file_observation(source: dict[str, Any]) -> dict[str, Any]:
    file_locator = str(source.get("file_locator") or "").strip()
    file_signature = str(source.get("file_signature") or "").strip()
    if not (file_locator and file_signature):
        return {}
    return {
        "file_locator": _item_id(file_locator, field="file locator"),
        "file_signature": _item_id(file_signature, field="file signature"),
        "file_generation": _lenient_count(source.get("file_generation")),
    }


def _watched_subject_observations(source: dict[str, Any]) -> dict[str, Any]:
    """One observation per watched Resident of a source that watches several."""

    subjects = source.get("subjects")
    if not isinstance(subjects, dict):
        return {}
    normalized_subjects: dict[str, dict[str, Any]] = {}
    for raw_subject, raw_state in list(subjects.items())[:MAX_WATCHED_RESIDENTS]:
        subject = _item_id(raw_subject, field="watched Resident")
        state = raw_state if isinstance(raw_state, dict) else {}
        normalized_state = _normalized_observation(state)
        if state.get("rebaseline"):
            normalized_state["rebaseline"] = True
        if normalized_state:
            normalized_subjects[subject] = normalized_state
    return {"subjects": normalized_subjects} if normalized_subjects else {}


def _countdown_timer(source: dict[str, Any]) -> dict[str, Any]:
    timer_due_at = str(source.get("timer_due_at") or "").strip()
    timer_started_at = str(source.get("timer_started_at") or "").strip()
    if not (timer_due_at and timer_started_at):
        return {}
    return {
        "timer_due_at": _item_id(timer_due_at, field="timer due_at"),
        "timer_started_at": _item_id(timer_started_at, field="timer started_at"),
        "timer_delay_seconds": max(1, int(source.get("timer_delay_seconds") or 1)),
        "timer_generation": max(0, int(source.get("timer_generation") or 0)),
    }


def _recurring_schedule_state(source: dict[str, Any]) -> dict[str, Any]:
    """The next occurrence of a recurring source and the one being committed."""

    recurring_contract = str(source.get("recurring_contract") or "").strip()
    next_scheduled_at = str(source.get("next_scheduled_at") or "").strip()
    if not (recurring_contract and next_scheduled_at):
        return {}
    result: dict[str, Any] = {
        "recurring_contract": _item_id(recurring_contract, field="recurring schedule contract"),
        "next_scheduled_at": _item_id(next_scheduled_at, field="next scheduled_at"),
        "next_due_at": _item_id(source.get("next_due_at"), field="next due_at"),
    }
    next_missed_count = max(0, int(source.get("next_missed_count") or 0))
    if next_missed_count:
        result["next_missed_count"] = next_missed_count
    result["generation"] = max(0, int(source.get("generation") or 0))
    last_scheduled_at = _optional_id(source.get("last_scheduled_at"), field="last scheduled_at")
    if last_scheduled_at:
        result["last_scheduled_at"] = last_scheduled_at
    pending_scheduled_at = _optional_id(source.get("pending_scheduled_at"), field="pending scheduled_at")
    if pending_scheduled_at:
        result.update({
            "pending_scheduled_at": pending_scheduled_at,
            "pending_next_scheduled_at": _item_id(
                source.get("pending_next_scheduled_at"), field="pending next scheduled_at",
            ),
            "pending_due_at": _item_id(source.get("pending_due_at"), field="pending due_at"),
            "pending_next_due_at": _item_id(source.get("pending_next_due_at"), field="pending next due_at"),
            "pending_observed_at": _item_id(source.get("pending_observed_at"), field="pending observed_at"),
            "pending_missed_count": max(0, int(source.get("pending_missed_count") or 0)),
            "pending_generation": max(0, int(source.get("pending_generation") or 0)),
        })
    return result


def _normalized_event_source(source: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "consumed_cursors": _unique_ids(source.get("consumed_cursors"), field="event cursor")[
            -MAX_EVENT_CURSORS:
        ],
    }
    if source.get("paused"):
        result["paused"] = True
        result["paused_at"] = str(source.get("paused_at") or "")
    if source.get("rebaseline"):
        result["rebaseline"] = True
    result.update(_fire_budget_state(source))
    if "fanout_settled_generation" in source:
        result["fanout_settled_generation"] = int(source["fanout_settled_generation"])
    if isinstance(source.get("last_delivery"), dict):
        result["last_delivery"] = _last_delivery_state(source["last_delivery"])
    result.update(_file_observation(source))
    result.update(_normalized_observation(source))
    result.update(_watched_subject_observations(source))
    result.update(_countdown_timer(source))
    result.update(_recurring_schedule_state(source))
    return result


def _normalized_pending_event(wire_id: str, raw_pending: object) -> dict[str, Any]:
    """One Event waiting on a wire; one held for manual recovery is kept as it is."""

    if not isinstance(raw_pending, dict):
        raise ValueError("pending Event wire item must be a dict")
    if raw_pending.get("recovery_blocked"):
        return deepcopy(raw_pending)
    event = deepcopy(raw_pending.get("event")) if isinstance(raw_pending.get("event"), dict) else {}
    event_id = _item_id(event.get("id"), field="pending event id")
    event_type = _item_id(event.get("type"), field="pending event type")
    event["id"] = event_id
    event["type"] = event_type
    pending: dict[str, Any] = {
        "event_source_id": _item_id(raw_pending.get("event_source_id"), field="pending event source id"),
        "cursor": _item_id(raw_pending.get("cursor"), field="pending event cursor"),
        "subscriber_id": _item_id(
            raw_pending.get("subscriber_id") or wire_id, field="pending event subscriber",
        ),
        "claim_token": _item_id(raw_pending.get("claim_token"), field="pending event claim token"),
        "event": event,
        "queued_at": _item_id(raw_pending.get("queued_at"), field="pending event queued_at"),
        "expires_at": str(raw_pending.get("expires_at") or "").strip(),
    }
    if isinstance(raw_pending.get("invocation"), dict):
        pending["invocation"] = _normalize_pending_delivery(raw_pending["invocation"])
    pending["revision"] = max(0, int(raw_pending.get("revision") or 0))
    if "prompt_override" in raw_pending:
        pending["prompt_override"] = lineage_primitives.normalize_invocation_prompt(raw_pending["prompt_override"])
    dispatch_token = _optional_id(raw_pending.get("dispatch_token"), field="pending event dispatch token")
    if dispatch_token:
        pending["dispatch_token"] = dispatch_token
        pending["dispatch_started_at"] = str(raw_pending.get("dispatch_started_at") or "").strip()
    pending["attempt_count"] = max(0, int(raw_pending.get("attempt_count") or 0))
    last_attempt_at = _optional_id(
        raw_pending.get("last_attempt_at"), field="pending event last attempt timestamp",
    )
    if last_attempt_at:
        pending["last_attempt_at"] = last_attempt_at
    last_error = str(raw_pending.get("last_error") or "").strip()
    if last_error:
        pending["last_error"] = last_error[:1000]
    retry_at = _optional_id(raw_pending.get("retry_at"), field="pending event retry timestamp")
    if retry_at:
        pending["retry_at"] = retry_at
    return pending


def _normalized_expired_delivery(raw_expired: dict[str, Any]) -> dict[str, Any]:
    if raw_expired.get("recovery_blocked"):
        return deepcopy(raw_expired)
    event = deepcopy(raw_expired.get("event")) if isinstance(raw_expired.get("event"), dict) else {}
    return {
        "event_source_id": _item_id(raw_expired.get("event_source_id"), field="expired event source id"),
        "wire_id": _item_id(raw_expired.get("wire_id"), field="expired event wire id"),
        "cursor": _item_id(raw_expired.get("cursor"), field="expired event cursor"),
        "event": event,
        "expired_at": _item_id(raw_expired.get("expired_at"), field="expired event timestamp"),
    }


def _mapping(item: dict[str, Any], field: str) -> dict[str, Any]:
    value = item.get(field)
    return value if isinstance(value, dict) else {}


def _normalize(raw: object) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    result = empty_runtime()
    for raw_source_id, raw_source in _mapping(item, "event_sources").items():
        source_id = _item_id(raw_source_id, field="event_source_id")
        result["event_sources"][source_id] = _normalized_event_source(
            raw_source if isinstance(raw_source, dict) else {}
        )
    for raw_wire_id, raw_wire in _mapping(item, "event_wires").items():
        wire_id = _item_id(raw_wire_id, field="event wire id")
        wire = raw_wire if isinstance(raw_wire, dict) else {}
        pending_events = [
            _normalized_pending_event(wire_id, raw_pending)
            for raw_pending in wire.get("pending_events") or []
        ]
        if pending_events:
            result["event_wires"][wire_id] = {"pending_events": pending_events}
    result["expired_deliveries"] = [
        _normalized_expired_delivery(raw_expired)
        for raw_expired in item.get("expired_deliveries") or []
        if isinstance(raw_expired, dict)
    ]
    result["operators"] = {
        _item_id(operator_id, field="Program operator id"): deepcopy(state)
        for operator_id, state in _mapping(item, "operators").items()
        if isinstance(state, dict)
    }
    result["delivery_profiles"] = {
        _item_id(message_id, field="delivery message id"): _delivery_profile(profile)
        for message_id, profile in _mapping(item, "delivery_profiles").items()
        if isinstance(profile, dict)
    }
    return result


def _delivery_profile(raw: dict[str, Any]) -> dict[str, Any]:
    return {"isolated": bool(raw.get("isolated"))}


def record_delivery_profile(lineage_id: str, message_id: str, profile: dict[str, Any]) -> None:
    """Remember how the Resident should run one Event delivery."""

    clean_message_id = _item_id(message_id, field="delivery message id")
    with _runtime_transaction(lineage_id) as runtime:
        profiles = runtime.setdefault("delivery_profiles", {})
        profiles.pop(clean_message_id, None)
        profiles[clean_message_id] = _delivery_profile(profile)
    cleanup_delivery_profiles(lineage_id=lineage_id)


def cleanup_delivery_profiles(*, lineage_id: str = "", message_id: str = "") -> int:
    """Reclaim configurations after all readers of the terminal input have finished."""
    return get_lineage_data_store().database.write(
        lambda connection: program_work_storage.prune_delivery_profiles(
            connection, lineage_id=lineage_id, message_id=message_id,
        )
    )


def delivery_profile(lineage_id: str, message_id: str) -> dict[str, Any]:
    """How the Resident runs one Event delivery; empty for any other input."""

    clean_lineage_id = str(lineage_id or "").strip()
    clean_message_id = str(message_id or "").strip()
    if not clean_lineage_id or not clean_message_id:
        return {}
    return dict(_read_object(clean_lineage_id, "delivery_profiles", clean_message_id) or {})


def _normalize_object(field: str, key: str, value: Any) -> Any:
    """Normalize one stored object exactly as it would be within the whole runtime."""

    if field == "expired_deliveries":
        return _normalize({field: value})[field]
    return next(iter(_normalize({field: {key: value}})[field].values()), None)


@contextmanager
def _runtime_transaction(lineage_id: str) -> Iterator[program_work_storage.ProgramWork]:
    """Mutate the objects the caller touches; each is locked only by its own users."""

    clean_id = _item_id(lineage_id, field="lineage_id")
    with program_work_storage.transaction(
        get_lineage_data_store().database, clean_id, normalize=_normalize_object,
    ) as runtime:
        yield runtime
    if runtime.changed:
        from .changes import lineage_changed

        lineage_changed(clean_id, runtime=True)


def claim_event(
    lineage_id: str,
    event_source_id: str,
    cursor: str,
) -> bool:
    """Atomically claim one source occurrence.

    The cursor is only a bounded dedupe token. It contains no reply,
    Workspace content, file body, Packet, or replayable snapshot.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_cursor = _item_id(cursor, field="event cursor")
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        cursors = list(source.get("consumed_cursors") or [])
        if clean_cursor in cursors:
            return False
        cursors.append(clean_cursor)
        source["consumed_cursors"] = cursors[-MAX_EVENT_CURSORS:]
    return True


def claim_event_occurrence(
    lineage_id: str,
    event_source_id: str,
    cursor: str,
    *,
    max_fires: int | None = None,
) -> dict[str, Any]:
    """Claim one Event Source occurrence before it fans out to wires.

    Occurrence count belongs to the Event Source, not to its delivery routes.
    Every accepted fact increments it exactly once, including for an
    unlimited or unrouted source. Every subscriber route then receives the
    same claimed occurrence, so adding or removing output wires never changes
    the count. ``max_fires`` only decides whether a new fact may be accepted.

    ``fire_count`` remains the persisted scalar.
    Public runtime projections expose the same value as
    the semantic ``occurrence_count``.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_cursor = _item_id(cursor, field="Event fire cursor")
    limit = normalize_optional_limit(max_fires)
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {
                "consumed_cursors": [],
                "fired_cursors": [],
                "fire_count": 0,
                "fire_generation": 0,
            },
        )
        fired_cursors = list(source.get("fired_cursors") or [])
        count = max(0, int(source.get("fire_count") or 0))
        generation = max(0, int(source.get("fire_generation") or 0))
        if source.get("paused"):
            # A paused source produces no new Event (decision_68215b3a825b).
            return {
                "status": "paused",
                "accepted": False,
                "occurrence_count": count,
                "max_fires": limit,
                "remaining_fires": limit_remaining(limit, count),
                "generation": generation,
                "exhausted": False,
            }
        if clean_cursor in fired_cursors:
            return {
                "status": "duplicate",
                "accepted": True,
                "occurrence_count": count,
                "max_fires": limit,
                "remaining_fires": limit_remaining(limit, count),
                "generation": generation,
                "exhausted": limit_reached(limit, count),
            }
        if limit_reached(limit, count):
            return {
                "status": "exhausted",
                "accepted": False,
                "occurrence_count": count,
                "max_fires": limit,
                "remaining_fires": 0,
                "generation": generation,
                "exhausted": True,
            }
        fired_cursors.append(clean_cursor)
        source.pop("fanout_settled_generation", None)
        count += 1
        source["fired_cursors"] = fired_cursors[
            -MAX_EVENT_FIRE_CURSORS:
        ]
        source["fire_count"] = count
        source["fire_generation"] = generation
        source["last_fired_at"] = _iso_now()
        return {
            "status": "fired",
            "accepted": True,
            "occurrence_count": count,
            "max_fires": limit,
            "remaining_fires": limit_remaining(limit, count),
            "generation": generation,
            "exhausted": limit_reached(limit, count),
            "last_fired_at": source["last_fired_at"],
        }


def reset_event_source_fire_budget(
    lineage_id: str,
    event_source_id: str,
    *,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    """Re-arm one Event Source without replaying an old occurrence.

    Fired cursors remain as bounded dedupe evidence. File/state observation
    baselines are cleared so the next reconciliation starts from the current
    live value instead of emitting a change that happened while exhausted.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {
                "consumed_cursors": [],
                "fired_cursors": [],
                "fire_count": 0,
                "fire_generation": 0,
            },
        )
        generation = max(0, int(source.get("fire_generation") or 0))
        if (
            expected_generation is not None
            and generation != max(0, int(expected_generation))
        ):
            return {
                "status": "stale",
                "occurrence_count": max(
                    0,
                    int(source.get("fire_count") or 0),
                ),
                "generation": generation,
            }
        source["fire_count"] = 0
        source["fire_generation"] = generation + 1
        source.pop("last_fired_at", None)
        source.pop("last_delivery", None)
        source.pop("fanout_settled_generation", None)
        for field in (
            "file_locator",
            "file_signature",
            "file_generation",
            "state_locator",
            "state_signature",
            "state_value",
            "state_generation",
            "state_epoch",
            "completion_locator",
            "completion_message_id",
            "completion_at",
            "completion_generation",
            "timer_due_at",
            "timer_started_at",
            "timer_delay_seconds",
            "timer_generation",
        ):
            source.pop(field, None)
        return {
            "status": "reset",
            "occurrence_count": 0,
            "generation": generation + 1,
        }


def mark_event_fanout_complete(
    lineage_id: str,
    event_source_id: str,
    *,
    generation: int,
    occurrence_count: int,
) -> None:
    """Mark completed fanout without allowing an old dispatch to retire a reset source."""

    source_id = _item_id(event_source_id, field="event_source_id")
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if (
            source is not None
            and int(source.get("fire_generation") or 0) == generation
            and int(source.get("fire_count") or 0) == occurrence_count
        ):
            source["fanout_settled_generation"] = generation


def record_event_delivery_summary(
    lineage_id: str,
    event_source_id: str,
    deliveries: list[dict[str, Any]] | None,
    *,
    event_id: str = "",
    trigger_path_id: str = "",
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep the latest Event delivery outcome for the runtime UI and record this one."""

    source_id = _item_id(event_source_id, field="event_source_id")
    statuses: dict[str, int] = {}
    for item in deliveries or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown").strip() or "unknown"
        status = _item_id(status, field="delivery status")
        statuses[status] = statuses.get(status, 0) + 1
    queued_statuses = {"queued", "replaced"}
    dropped_statuses = {"dropped", "queue_full", "discarded"}
    failed_statuses = {
        "failed",
        "unavailable",
        "program_incomplete",
        "budget_full",
        "cycle_finishing",
    }
    summary = {
        "at": _iso_now(),
        "total_count": sum(statuses.values()),
        "delivered_count": sum(
            count
            for status, count in statuses.items()
            if status not in queued_statuses | dropped_statuses | failed_statuses
        ),
        "queued_count": sum(statuses.get(status, 0) for status in queued_statuses),
        "dropped_count": sum(statuses.get(status, 0) for status in dropped_statuses),
        "failed_count": sum(statuses.get(status, 0) for status in failed_statuses),
        "statuses": statuses,
    }
    if str(event_id or "").strip():
        summary["event_id"] = _item_id(
            event_id, field="Event delivery event id"
        )
    if str(trigger_path_id or "").strip():
        summary["trigger_path_id"] = _item_id(
            trigger_path_id, field="Event delivery Trigger Path id"
        )
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        source["last_delivery"] = summary
    return deepcopy(summary)


EFFECT_CALL_SUMMARY_FIELDS = ("position", "index", "line", "instruction", "instruction_id", "status", "error_code", "script_run_id")


def _effect_call_summary(raw: object) -> list[dict[str, Any]]:
    """The calls of an Effect's latest activation, bounded to the script call limit."""

    from .lineage_effect_script import MAX_CALLS

    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        entry = {
            name: (int(item.get(name) or 0) if name in {"index", "line"} else str(item.get(name) or "")[:200])
            for name in EFFECT_CALL_SUMMARY_FIELDS
            if name in item
        }
        result.append(entry)
        if len(result) >= MAX_CALLS:
            break
    return result


def effect_result_projection(
    lineage_id: str,
    effect_id: str,
    *,
    status: str,
    outputs: dict[str, Any] | None = None,
    instruction_id: str = "",
    instruction: str = "",
    error: str = "",
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the dispatch result; the Effect journal owns terminal persistence."""

    clean_effect_id = _item_id(effect_id, field="Program Effect id")
    clean_status = str(status or "").strip().lower()
    if clean_status not in {"executed", "failed"}:
        raise ValueError("Program Effect execution status is invalid")
    projection = {
        "status": clean_status,
        "at": _iso_now(),
        "instruction_id": str(instruction_id or "").strip(),
        "instruction": str(instruction or "").strip(),
        "outputs": deepcopy(outputs) if isinstance(outputs, dict) else {},
        "calls": _effect_call_summary(calls),
    }
    diagnostic = str(error or "").strip()
    if diagnostic:
        projection["error"] = diagnostic[:1000]
    return deepcopy(projection)


def effect_runtime_view(
    lineage_id: str,
    effect_ids: list[str] | tuple[str, ...] | set[str],
) -> dict[str, dict[str, Any]]:
    """Read durable controls and the latest completed Effect activation."""
    ids = [_item_id(value, field="Effect id") for value in effect_ids]
    if not ids:
        return {}
    with get_lineage_data_store().database.read_current() as connection:
        # Each Effect's newest finished step, not a sort of its whole history.
        rows = connection.execute(
            "SELECT effect.node_id,s.step_id,s.trigger_path_id,s.status,s.finished_at,s.result_json "
            "FROM unnest(%s::text[]) AS effect(node_id) CROSS JOIN LATERAL ("
            "SELECT s.* FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
            "WHERE s.node_id=effect.node_id AND s.kind='effect' AND s.status IN ('completed','failed','cancelled') "
            "AND p.lineage_id=%s ORDER BY s.finished_at DESC,s.step_id DESC LIMIT 1) s", (ids, lineage_id),
        ).fetchall()
        controls = connection.execute(
            "SELECT operator_id,checkpoint_json FROM operator_work WHERE lineage_id=%s AND operator_id=ANY(%s)",
            (lineage_id, ids),
        ).fetchall()
    result = {}
    for row in rows:
        record = json_value(row["result_json"])
        calls = list((record.get("calls") or {}).values())
        last = calls[-1] if calls else {}
        failed = row["status"] == "failed"
        result[row["node_id"]] = {
            "status": "failed" if failed else "cancelled" if row["status"] == "cancelled" else "executed",
            "step_id": row["step_id"],
            "trigger_path_id": row["trigger_path_id"],
            "at": iso_time(row["finished_at"]),
            "instruction_id": last.get("instruction_id", ""),
            "instruction": last.get("instruction", ""),
            "outputs": record.get("outputs") or {},
            "calls": _effect_call_summary(calls),
        }
        if failed:
            result[row["node_id"]]["error"] = str((record.get("outputs") or {}).get("diagnostic") or record.get("failure") or "")[:1000]
    states = {row["operator_id"]: json_value(row["checkpoint_json"]) for row in controls}
    for effect_id in ids:
        state = states.get(effect_id, {})
        paused = bool(state.get("paused"))
        result.setdefault(effect_id, {}).update(
            paused=paused, state="paused" if paused else "active", paused_at=str(state.get("paused_at") or ""),
        )
    return result


def ensure_countdown_timer(
    lineage_id: str,
    event_source_id: str,
    *,
    delay_seconds: int,
) -> dict[str, Any]:
    """Anchor one countdown in runtime so graph refreshes cannot restart it."""

    source_id = _item_id(event_source_id, field="event_source_id")
    delay = max(1, min(7 * 24 * 60 * 60, int(delay_seconds or 1)))
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        previous_delay = max(
            0,
            int(source.get("timer_delay_seconds") or 0),
        )
        due_at = lineage_recurring_schedule.parse_utc(source.get("timer_due_at"))
        started_at = lineage_recurring_schedule.parse_utc(source.get("timer_started_at"))
        generation = max(
            0,
            int(source.get("timer_generation") or 0),
        )
        if due_at is None or started_at is None or previous_delay != delay:
            started_at = datetime.now(timezone.utc)
            due_at = started_at + timedelta(seconds=delay)
            generation += 1
            source["timer_started_at"] = (
                started_at.isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            source["timer_due_at"] = (
                due_at.isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            source["timer_delay_seconds"] = delay
            source["timer_generation"] = generation
        return {
            "started_at": str(source.get("timer_started_at") or ""),
            "due_at": str(source.get("timer_due_at") or ""),
            "delay_seconds": delay,
            "generation": generation,
            "reached": bool(
                due_at is not None
                and due_at <= datetime.now(timezone.utc)
            ),
        }


def observe_recurring_schedule(
    lineage_id: str,
    event_source_id: str,
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Persist and observe one recurring schedule without replay ambiguity.

    At most one occurrence is pending at the source boundary. Its payload
    metadata is persisted before dispatch, so a restart retries the same
    generation/cursor. ``skip`` advances missed windows without producing an
    Event; ``coalesce_latest`` represents them with the latest due occurrence.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    normalized = lineage_recurring_schedule.normalize_config(config)
    paused_source = _read_object(lineage_id, "event_sources", source_id) or {}
    if paused_source.get("paused"):
        # A paused source skips due fires; resuming continues from the next
        # scheduled time without replaying the paused window.
        source = paused_source
        return {
            "status": "paused",
            "generation": max(0, int(source.get("generation") or 0)),
            "next_scheduled_at": str(source.get("next_scheduled_at") or ""),
            "next_due_at": str(source.get("next_due_at") or ""),
        }
    contract = lineage_recurring_schedule.fingerprint(normalized)
    now = datetime.now(timezone.utc)
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        previous_contract = str(source.get("recurring_contract") or "")
        generation = max(0, int(source.get("generation") or 0))
        next_at = lineage_recurring_schedule.parse_utc(
            source.get("next_scheduled_at")
        )
        changed = previous_contract != contract or next_at is None
        if changed:
            generation += 1
            next_at = lineage_recurring_schedule.first_after(normalized, now)
            source["recurring_contract"] = contract
            source["generation"] = generation
            source["next_scheduled_at"] = (
                lineage_recurring_schedule.iso_utc(next_at)
            )
            source["next_due_at"] = lineage_recurring_schedule.iso_utc(
                lineage_recurring_schedule.sample_due_at(normalized, next_at)
            )
            source.pop("last_scheduled_at", None)
            for field in (
                "next_missed_count",
                "pending_scheduled_at",
                "pending_due_at",
                "pending_next_scheduled_at",
                "pending_next_due_at",
                "pending_observed_at",
                "pending_missed_count",
                "pending_generation",
            ):
                source.pop(field, None)

        pending_at = str(source.get("pending_scheduled_at") or "")
        pending_generation = max(
            0,
            int(source.get("pending_generation") or 0),
        )
        if pending_at and pending_generation == generation:
            return {
                "status": "due",
                "scheduled_at": pending_at,
                "due_at": str(source.get("pending_due_at") or ""),
                "observed_at": str(source.get("pending_observed_at") or ""),
                "missed_count": max(
                    0,
                    int(source.get("pending_missed_count") or 0),
                ),
                "generation": generation,
                "next_scheduled_at": str(
                    source.get("pending_next_scheduled_at") or ""
                ),
                "next_due_at": str(
                    source.get("pending_next_due_at")
                    or ""
                ),
            }

        assert next_at is not None
        next_due = lineage_recurring_schedule.parse_utc(source.get("next_due_at"))
        assert next_due is not None
        if next_due > now:
            return {
                "status": "waiting",
                "last_scheduled_at": str(
                    source.get("last_scheduled_at") or ""
                ),
                "next_scheduled_at": str(source["next_scheduled_at"]),
                "next_due_at": str(source["next_due_at"]),
                "generation": generation,
            }
        carried_missed = max(0, int(source.get("next_missed_count") or 0))
        if carried_missed:
            # A coalesced candidate was already selected and its due time
            # persisted. Fire it before considering newer nominal windows.
            latest = next_at
            due_count = carried_missed + 1
            future = lineage_recurring_schedule.first_after(normalized, latest)
        else:
            latest, due_count, future = lineage_recurring_schedule.due_window(
                normalized,
                next_at,
                now,
            )
        if normalized["misfire_policy"] == "skip" and due_count > 1:
            latest_due = latest
            if latest + timedelta(seconds=normalized["jitter_seconds"]) > now:
                latest_due = lineage_recurring_schedule.sample_due_at(
                    normalized, latest
                )
            if latest_due > now:
                previous, skipped_count, _ = lineage_recurring_schedule.due_window(
                    normalized,
                    next_at,
                    latest - timedelta(microseconds=1),
                )
                source["last_scheduled_at"] = lineage_recurring_schedule.iso_utc(previous)
                source["next_scheduled_at"] = lineage_recurring_schedule.iso_utc(latest)
                source["next_due_at"] = lineage_recurring_schedule.iso_utc(latest_due)
                return {
                    "status": "skipped",
                    "skipped_count": skipped_count,
                    "last_scheduled_at": source["last_scheduled_at"],
                    "next_scheduled_at": source["next_scheduled_at"],
                    "generation": generation,
                }
            source["last_scheduled_at"] = (
                lineage_recurring_schedule.iso_utc(latest)
            )
            source["next_scheduled_at"] = (
                lineage_recurring_schedule.iso_utc(future)
            )
            source["next_due_at"] = lineage_recurring_schedule.iso_utc(
                lineage_recurring_schedule.sample_due_at(normalized, future)
            )
            source.pop("next_missed_count", None)
            return {
                "status": "skipped",
                "skipped_count": due_count,
                "last_scheduled_at": source["last_scheduled_at"],
                "next_scheduled_at": source["next_scheduled_at"],
                "generation": generation,
            }

        if due_count > 1 and not carried_missed:
            # Coalesce nominal windows first, then sample and persist the
            # selected occurrence's actual due time. A later poll must reuse
            # that sample even when another nominal window has begun.
            next_at = latest
            next_due = lineage_recurring_schedule.sample_due_at(normalized, latest)
            source["next_scheduled_at"] = lineage_recurring_schedule.iso_utc(latest)
            source["next_due_at"] = lineage_recurring_schedule.iso_utc(next_due)
            source["next_missed_count"] = due_count - 1
            if next_due > now:
                return {
                    "status": "waiting",
                    "last_scheduled_at": str(source.get("last_scheduled_at") or ""),
                    "next_scheduled_at": source["next_scheduled_at"],
                    "next_due_at": source["next_due_at"],
                    "generation": generation,
                }

        scheduled_at = lineage_recurring_schedule.iso_utc(latest)
        due_at = str(source["next_due_at"])
        observed_at = lineage_recurring_schedule.iso_utc(now)
        next_scheduled_at = lineage_recurring_schedule.iso_utc(future)
        next_due_at = lineage_recurring_schedule.iso_utc(
            lineage_recurring_schedule.sample_due_at(normalized, future)
        )
        source.update(
            {
                "pending_scheduled_at": scheduled_at,
                "pending_due_at": due_at,
                "pending_next_scheduled_at": next_scheduled_at,
                "pending_next_due_at": next_due_at,
                "pending_observed_at": observed_at,
                "pending_missed_count": due_count - 1,
                "pending_generation": generation,
            }
        )
        return {
            "status": "due",
            "scheduled_at": scheduled_at,
            "due_at": due_at,
            "observed_at": observed_at,
            "missed_count": due_count - 1,
            "generation": generation,
            "next_scheduled_at": next_scheduled_at,
            "next_due_at": next_due_at,
        }


def commit_recurring_schedule(
    lineage_id: str,
    event_source_id: str,
    *,
    generation: int,
    scheduled_at: str,
) -> dict[str, Any]:
    """Advance one pending occurrence, fenced by schedule generation."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_scheduled_at = _item_id(scheduled_at, field="scheduled_at")
    expected_generation = max(0, int(generation))
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id) or {}
        if (
            max(0, int(source.get("generation") or 0))
            != expected_generation
            or str(source.get("pending_scheduled_at") or "")
            != clean_scheduled_at
            or max(0, int(source.get("pending_generation") or 0))
            != expected_generation
        ):
            return {"status": "stale", "generation": max(
                0, int(source.get("generation") or 0)
            )}
        source["last_scheduled_at"] = clean_scheduled_at
        source["next_scheduled_at"] = str(
            source.get("pending_next_scheduled_at") or ""
        )
        source["next_due_at"] = str(
            source.get("pending_next_due_at") or ""
        )
        source.pop("next_missed_count", None)
        for field in (
            "pending_scheduled_at",
            "pending_due_at",
            "pending_next_scheduled_at",
            "pending_next_due_at",
            "pending_observed_at",
            "pending_missed_count",
            "pending_generation",
        ):
            source.pop(field, None)
        return {
            "status": "advanced",
            "generation": expected_generation,
            "last_scheduled_at": source["last_scheduled_at"],
            "next_scheduled_at": source["next_scheduled_at"],
            "next_due_at": source["next_due_at"],
        }


def _subscriber_cursor(
    cursor: str,
    subscriber_id: str,
    generation: int = 0,
) -> str:
    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id,
        field="event subscriber",
    )
    digest = hashlib.sha256(clean_cursor.encode("utf-8")).hexdigest()
    return (
        f"subscriber:{clean_subscriber_id}:generation:"
        f"{max(0, int(generation))}:{digest}"
    )


def _subscriber_cursor_matches_occurrence(
    token: object,
    cursor: str,
    subscriber_id: str,
) -> bool:
    """Match one durable occurrence across every lifecycle generation."""

    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id,
        field="event subscriber",
    )
    digest = hashlib.sha256(clean_cursor.encode("utf-8")).hexdigest()
    return (
        str(token).startswith(
            f"subscriber:{clean_subscriber_id}:generation:"
        )
        and str(token).endswith(f":{digest}")
    )


def _subscriber_occurrence_claimed(
    cursors: list[str],
    cursor: str,
    subscriber_id: str,
    generation: int | None,
) -> bool:
    if generation is not None:
        return _subscriber_cursor(
            cursor,
            subscriber_id,
            generation,
        ) in set(cursors)
    return any(
        _subscriber_cursor_matches_occurrence(
            token,
            cursor,
            subscriber_id,
        )
        for token in cursors
    )


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _prune_expired_pending_events(
    runtime: dict[str, Any],
    *,
    lineage_id: str,
    now: datetime | None = None,
    wire_id: str = "",
) -> int:
    """Move expired queued deliveries to the expiry notices.

    A delivery of one wire prunes only that wire: other wires stay unlocked,
    and the backlog drain prunes every wire.
    """
    current = now or datetime.now(timezone.utc)
    removed = 0
    wires = runtime.get("event_wires", {})
    selected = (
        [(wire_id, wires[wire_id])] if wire_id and wire_id in wires
        else [] if wire_id else list(wires.items())
    )
    for wire_id, wire in selected:
        if not isinstance(wire, dict):
            runtime["event_wires"].pop(wire_id, None)
            continue
        retained: list[dict[str, Any]] = []
        for pending in wire.get("pending_events") or []:
            if pending.get("recovery_blocked"):
                retained.append(pending)
                continue
            expires_at = lineage_recurring_schedule.parse_utc(
                pending.get("expires_at")
                if isinstance(pending, dict)
                else ""
            )
            if expires_at is not None and expires_at <= current:
                removed += 1
                if isinstance(pending, dict):
                    expired = {
                        "event_source_id": str(
                            pending.get("event_source_id") or ""
                        ),
                        "wire_id": wire_id,
                        "cursor": str(pending.get("cursor") or ""),
                        "event": deepcopy(pending.get("event") or {}),
                        "expired_at": _iso_now(),
                    }
                    runtime.setdefault("expired_deliveries", []).append(expired)
                    trigger_paths.delivery_settled(lineage_id, wire_id, pending, status="failed")
                continue
            retained.append(pending)
        if retained:
            wire["pending_events"] = retained
        else:
            runtime["event_wires"].pop(wire_id, None)
    return removed


def drain_expired_deliveries(
    lineage_id: str,
) -> list[dict[str, Any]]:
    """Claim TTL expirations not yet notified; their records stay in trigger_paths."""

    with _runtime_transaction(lineage_id) as runtime:
        _prune_expired_pending_events(runtime, lineage_id=lineage_id)
        expired = [p for p in runtime.get("expired_deliveries") or [] if not p.get("recovery_blocked")]
        runtime["expired_deliveries"] = [p for p in runtime.get("expired_deliveries") or [] if p.get("recovery_blocked")]
        return deepcopy(expired)


def defer_event_for_wire(
    lineage_id: str,
    event_source_id: str,
    wire_id: str,
    cursor: str,
    event: dict[str, Any],
    *,
    policy: str,
    queue_limit: int = 10,
    expires_after_seconds: int = 0,
    subscriber_id: str = "",
    subscriber_generation: int | None = None,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consume or retain one occurrence when this wire cannot deliver now.

    Claims and queue mutation share one database transaction. The queue stores
    only the Event envelope and delivery cursor; it never snapshots a Resident
    context or Workspace.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_wire_id = _item_id(wire_id, field="event wire id")
    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id or clean_wire_id,
        field="event subscriber",
    )
    clean_policy = str(policy or "").strip().lower() or "drop"
    if clean_policy not in {"drop", "latest", "queue"}:
        raise ValueError("event unavailable policy is invalid")
    clean_event = deepcopy(event) if isinstance(event, dict) else {}
    _item_id(clean_event.get("id"), field="pending event id")
    _item_id(clean_event.get("type"), field="pending event type")
    limit = max(1, min(MAX_PENDING_EVENTS_PER_WIRE, int(queue_limit or 10)))
    ttl = max(0, int(expires_after_seconds or 0))
    claim_generation = (
        max(0, int(subscriber_generation))
        if subscriber_generation is not None
        else None
    )
    claim_token = _subscriber_cursor(
        clean_cursor,
        clean_subscriber_id,
        claim_generation or 0,
    )
    with _runtime_transaction(lineage_id) as runtime:
        # The source is locked before the wire (program_work_storage.LOCK_ORDER).
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        _prune_expired_pending_events(runtime, lineage_id=lineage_id, wire_id=clean_wire_id)
        cursors = list(source.get("consumed_cursors") or [])
        if _subscriber_occurrence_claimed(
            cursors,
            clean_cursor,
            clean_subscriber_id,
            claim_generation,
        ):
            return {
                "status": "duplicate",
                "wire_id": clean_wire_id,
                "pending_count": len(
                    (
                        runtime.get("event_wires", {})
                        .get(clean_wire_id, {})
                        .get("pending_events", [])
                    )
                ),
            }
        cursors.append(claim_token)
        source["consumed_cursors"] = cursors[-MAX_EVENT_CURSORS:]
        if clean_policy == "drop":
            return {
                "status": "dropped",
                "wire_id": clean_wire_id,
                "pending_count": 0,
            }

        now = datetime.now(timezone.utc)
        expires_at = (
            (now + timedelta(seconds=ttl))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
            if ttl
            else ""
        )
        pending = {
            "event_source_id": source_id,
            "cursor": clean_cursor,
            "subscriber_id": clean_subscriber_id,
            "claim_token": claim_token,
            "event": clean_event,
            "queued_at": (
                now.isoformat(timespec="seconds").replace("+00:00", "Z")
            ),
            "expires_at": expires_at,
        }
        if isinstance(invocation, dict):
            pending["invocation"] = _normalize_pending_delivery(invocation)
        wire = runtime["event_wires"].setdefault(
            clean_wire_id,
            {"pending_events": []},
        )
        pending_events = list(wire.get("pending_events") or [])
        replaced_count = 0
        if clean_policy == "latest":
            replaced_count = len(pending_events)
            for previous in pending_events:
                trigger_paths.delivery_settled(lineage_id, clean_wire_id, previous, status="skipped")
            pending_events = [pending]
            status = "replaced" if replaced_count else "queued"
        elif len(pending_events) >= limit:
            return {
                "status": "queue_full",
                "wire_id": clean_wire_id,
                "pending_count": len(pending_events),
                "queue_limit": limit,
            }
        else:
            pending_events.append(pending)
            status = "queued"
        wire["pending_events"] = pending_events
        trigger_paths.delivery_waiting(lineage_id, clean_wire_id, pending)
        return {
            "status": status,
            "wire_id": clean_wire_id,
            "pending_count": len(pending_events),
            "replaced_count": replaced_count,
            "queue_limit": 1 if clean_policy == "latest" else limit,
            "expires_at": expires_at,
        }


def stage_event_delivery(
    lineage_id: str,
    event_source_id: str,
    wire_id: str,
    cursor: str,
    event: dict[str, Any],
    *,
    subscriber_id: str = "",
    subscriber_generation: int | None = None,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a wire delivery intent before invoking its downstream."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_wire_id = _item_id(wire_id, field="event wire id")
    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id or clean_wire_id,
        field="event subscriber",
    )
    clean_event = deepcopy(event) if isinstance(event, dict) else {}
    _item_id(clean_event.get("id"), field="pending event id")
    _item_id(clean_event.get("type"), field="pending event type")
    generation = (
        max(0, int(subscriber_generation))
        if subscriber_generation is not None
        else 0
    )
    claim_token = _subscriber_cursor(
        clean_cursor,
        clean_subscriber_id,
        generation,
    )
    clean_invocation: dict[str, Any] | None = None
    if isinstance(invocation, dict):
        clean_invocation = _normalize_pending_delivery(invocation)

    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        wire = runtime["event_wires"].setdefault(
            clean_wire_id,
            {"pending_events": []},
        )
        pending_events = list(wire.get("pending_events") or [])
        if any(
            str(item.get("claim_token") or "") == claim_token
            for item in pending_events
        ):
            return {
                "status": "pending",
                "wire_id": clean_wire_id,
                "pending_count": len(pending_events),
            }

        cursors = list(source.get("consumed_cursors") or [])
        if _subscriber_occurrence_claimed(
            cursors,
            clean_cursor,
            clean_subscriber_id,
            subscriber_generation,
        ):
            if not pending_events:
                runtime["event_wires"].pop(clean_wire_id, None)
            return {
                "status": "delivered",
                "wire_id": clean_wire_id,
                "pending_count": len(pending_events),
            }

        cursors.append(claim_token)
        source["consumed_cursors"] = cursors[-MAX_EVENT_CURSORS:]
        now = _iso_now()
        pending: dict[str, Any] = {
            "event_source_id": source_id,
            "cursor": clean_cursor,
            "subscriber_id": clean_subscriber_id,
            "claim_token": claim_token,
            "event": clean_event,
            "queued_at": now,
            "expires_at": "",
            "attempt_count": 0,
        }
        if clean_invocation is not None:
            pending["invocation"] = clean_invocation
        pending_events.append(pending)
        wire["pending_events"] = pending_events
        trigger_paths.delivery_waiting(lineage_id, clean_wire_id, pending)
        return {
            "status": "staged",
            "wire_id": clean_wire_id,
            "pending_count": len(pending_events),
            "queued_at": now,
        }


def claim_pending_event(
    lineage_id: str,
    wire_id: str,
    *,
    cursor: str = "",
    event_source_id: str = "",
    target_id: str = "",
) -> dict[str, Any] | None:
    """Claim the FIFO head of one wire for a single delivery attempt.

    A parallel wire names the occurrence instead: it is claimed even while
    earlier ones are still being delivered.

    A paused component returns an admission fact without a dispatch token;
    callers can report the pause without claiming or incrementing attempts.
    """

    clean_wire_id = _item_id(wire_id, field="event wire id")
    with _runtime_transaction(lineage_id) as runtime:
        # Source -> wire -> target is the Program work lock order. Both live
        # and replay admission supply their compiled source/target identities.
        if event_source_id and runtime.get("event_sources", {}).get(event_source_id, {}).get("paused"):
            return {"status": "paused", "component_id": event_source_id}
        _prune_expired_pending_events(runtime, lineage_id=lineage_id, wire_id=clean_wire_id)
        wire = runtime.get("event_wires", {}).get(clean_wire_id)
        if not isinstance(wire, dict):
            return None
        pending_events = [p for p in wire.get("pending_events") or [] if not p.get("recovery_blocked")]
        if not pending_events:
            return None
        pending = (
            next((item for item in pending_events if str(item.get("cursor") or "") == cursor), None)
            if cursor else pending_events[0]
        )
        if pending is None:
            return None
        if event_source_id and str(pending.get("event_source_id") or "") != event_source_id:
            return None
        if target_id and runtime.get("operators", {}).get(target_id, {}).get("paused"):
            return {"status": "paused", "component_id": target_id}
        retry_at = lineage_recurring_schedule.parse_utc(pending.get("retry_at"))
        if retry_at is not None and retry_at > datetime.now(timezone.utc):
            # A failed attempt waits out its delay however often the Lineage is reconciled.
            return None
        if str(pending.get("dispatch_token") or ""):
            started_at = lineage_recurring_schedule.parse_utc(pending.get("dispatch_started_at"))
            if (
                started_at is not None
                and datetime.now(timezone.utc) - started_at
                < timedelta(
                    seconds=PENDING_EVENT_DISPATCH_LEASE_SECONDS
                )
            ):
                return None
            pending.pop("dispatch_token", None)
            pending.pop("dispatch_started_at", None)
        dispatch_token = f"dispatch_{secrets.token_hex(12)}"
        pending["dispatch_token"] = dispatch_token
        pending["dispatch_started_at"] = _iso_now()
        pending["last_attempt_at"] = pending["dispatch_started_at"]
        pending["attempt_count"] = max(
            0,
            int(pending.get("attempt_count") or 0),
        ) + 1
        return deepcopy(pending)


def hold_pending_occurrence(item: dict[str, Any], hold: dict[str, Any], at: str) -> None:
    """Release a delivery lease into a durable hold; never admit it as a retry."""
    item.pop("dispatch_token", None)
    item.pop("dispatch_started_at", None)
    item.pop("retry_at", None)
    item["recovery_blocked"] = {**deepcopy(hold), "at": at}


def finish_pending_event(
    lineage_id: str,
    wire_id: str,
    dispatch_token: str,
    *,
    delivered: bool,
    error: str = "",
    terminal_status: str = "completed",
    retry_later: bool = False,
    hold: dict[str, Any] | None = None,
) -> bool:
    """Settle one claimed delivery; an undelivered one stays queued.

    ``retry_later`` marks a failed attempt: the next one waits out
    ``retry_delay_seconds``. A target that is closed or busy is retried as
    soon as it changes. ``hold`` keeps an occurrence that no retry can
    settle out of retries until ResolveEffectActivation ends it; the wire
    delivers its later occurrences meanwhile.
    """
    clean_wire_id = _item_id(wire_id, field="event wire id")
    clean_dispatch_token = _item_id(
        dispatch_token,
        field="pending event dispatch token",
    )
    with _runtime_transaction(lineage_id) as runtime:
        wire = runtime.get("event_wires", {}).get(clean_wire_id)
        if not isinstance(wire, dict):
            return False
        pending_events = list(wire.get("pending_events") or [])
        matched = next(
            (
                item
                for item in pending_events
                if str(item.get("dispatch_token") or "")
                == clean_dispatch_token
            ),
            None,
        )
        if matched is None:
            return False
        if delivered:
            trigger_paths.delivery_settled(lineage_id, clean_wire_id, matched, status=terminal_status, error=error)
            wire["pending_events"] = [
                item
                for item in pending_events
                if item is not matched
            ]
        else:
            matched.pop("dispatch_token", None)
            matched.pop("dispatch_started_at", None)
            diagnostic = str(error or "").strip()
            if diagnostic:
                matched["last_error"] = diagnostic[:1000]
            matched.pop("retry_at", None)
            if hold:
                hold_pending_occurrence(matched, hold, _iso_now())
            elif retry_later:
                delay = retry_delay_seconds(int(matched.get("attempt_count") or 1))
                matched["retry_at"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(
                    timespec="seconds").replace("+00:00", "Z")
        if not wire.get("pending_events"):
            runtime["event_wires"].pop(clean_wire_id, None)
    return True


def settle_effect_delivery(lineage_id: str, activation: dict[str, Any]) -> None:
    """Release only the queued occurrence terminated by an admin disposition."""
    wire_id = str(activation.get("source_wire_id") or "")
    event_id = str(activation.get("event_id") or "")
    if not wire_id or not event_id:
        return
    with _runtime_transaction(lineage_id) as runtime:
        wire = runtime["event_wires"].get(wire_id)
        if wire is None:
            return
        wire["pending_events"] = [item for item in wire.get("pending_events", [])
                                  if str((item.get("event") or {}).get("id") or "") != event_id]
        if not wire["pending_events"]:
            runtime["event_wires"].pop(wire_id, None)


def pending_wire_deliveries(
    lineage_id: str,
    wire_id: str,
) -> list[dict[str, Any]]:
    wire_id = _item_id(wire_id, field="event wire id")
    runtime = read_runtime(lineage_id)
    now = datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    pending_events = (runtime["event_wires"].get(wire_id) or {}).get(
        "pending_events", [],
    )
    for pending in pending_events:
        expires_at = lineage_recurring_schedule.parse_utc(pending.get("expires_at"))
        if expires_at is not None and expires_at <= now and not pending.get("recovery_blocked"):
            continue
        result.append(
            {
                "delivery_id": pending["claim_token"],
                "revision": pending["revision"],
                "event_id": pending["event"]["id"],
                "event_type": pending["event"]["type"],
                "queued_at": pending["queued_at"],
                "expires_at": pending["expires_at"],
                "status": (
                    "blocked" if pending.get("recovery_blocked") else "dispatching" if pending.get("dispatch_token") else "waiting"
                ),
                "recovery_blocked": deepcopy(pending.get("recovery_blocked")),
                "prompt_override": pending.get("prompt_override"),
            }
        )
    return result


def update_pending_wire_delivery(
    lineage_id: str,
    wire_id: str,
    delivery_id: str,
    *,
    prompt: str = "",
    delete: bool = False,
) -> dict[str, Any]:
    """Mutate one unclaimed delivery without changing its consumed cursor."""

    wire_id = _item_id(wire_id, field="event wire id")
    delivery_id = _item_id(delivery_id, field="pending invocation id")
    if not isinstance(prompt, str):
        raise ValueError("投递 prompt 必须是字符串")
    prompt = lineage_primitives.normalize_invocation_prompt(prompt)
    with _runtime_transaction(lineage_id) as runtime:
        _prune_expired_pending_events(runtime, lineage_id=lineage_id, wire_id=wire_id)
        wire = runtime["event_wires"].get(wire_id) or {}
        pending_events = wire.get("pending_events") or []
        pending = next(
            (item for item in pending_events if item["claim_token"] == delivery_id),
            None,
        )
        if pending is None:
            raise KeyError("这条 投递 已投递、过期或被替换，请刷新队列")
        if pending.get("dispatch_token"):
            raise ValueError("这条 投递 正在投递，不能编辑或删除")
        if delete:
            from . import trigger_paths
            trigger_paths.delivery_settled(lineage_id, wire_id, pending, status="cancelled")
            wire["pending_events"] = [
                item for item in pending_events if item is not pending
            ]
            if not wire["pending_events"]:
                runtime["event_wires"].pop(wire_id, None)
        else:
            if prompt.strip():
                pending["prompt_override"] = prompt
            else:
                pending.pop("prompt_override", None)
            pending["revision"] = int(pending.get("revision") or 0) + 1
        return {
            "delivery_id": delivery_id,
            "status": "deleted" if delete else "updated",
        }


def forget_event_wire(
    lineage_id: str,
    wire_id: str,
) -> None:
    clean_wire_id = _item_id(wire_id, field="event wire id")
    with _runtime_transaction(lineage_id) as runtime:
        wire = runtime["event_wires"].get(clean_wire_id) or {}
        from . import trigger_paths
        for pending in wire.get("pending_events", []):
            trigger_paths.delivery_settled(lineage_id, clean_wire_id, pending, status="cancelled")
        runtime["event_wires"].pop(clean_wire_id, None)


def event_wire_runtime_view(
    lineage_id: str,
    wire_configs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return the small live queue projection used by the HATG edge UI."""

    runtime = read_runtime(lineage_id)
    states = runtime.get("event_wires") or {}
    result: dict[str, dict[str, Any]] = {}
    for raw_wire_id, raw_config in wire_configs.items():
        wire_id = _item_id(raw_wire_id, field="event wire id")
        config = raw_config if isinstance(raw_config, dict) else {}
        pending = list(
            (states.get(wire_id) or {}).get("pending_events") or []
        )
        now = datetime.now(timezone.utc)
        pending = [
            item
            for item in pending
            if not item.get("recovery_blocked") and (
                lineage_recurring_schedule.parse_utc(item.get("expires_at")) is None
                or lineage_recurring_schedule.parse_utc(item.get("expires_at")) > now
            )
        ]
        result[wire_id] = {
            "unavailable_policy": str(
                config.get("unavailable_policy") or "drop"
            ),
            "queue_limit": int(config.get("queue_limit") or 10),
            "expires_after_seconds": int(
                config.get("expires_after_seconds") or 0
            ),
            "pending_count": len(pending),
            "dispatching_count": sum(
                1
                for item in pending
                if str(item.get("dispatch_token") or "")
            ),
            "oldest_queued_at": str(
                (pending[0] if pending else {}).get("queued_at") or ""
            ),
            "oldest_cursor": str(
                (pending[0] if pending else {}).get("cursor") or ""
            ),
            "oldest_event_source_id": str(
                (pending[0] if pending else {}).get("event_source_id") or ""
            ),
            "attempt_count": sum(
                max(0, int(item.get("attempt_count") or 0))
                for item in pending
            ),
            "last_attempt_at": max(
                (
                    str(item.get("last_attempt_at") or "")
                    for item in pending
                ),
                default="",
            ),
            "last_error": next(
                (
                    str(item.get("last_error") or "")
                    for item in reversed(pending)
                    if str(item.get("last_error") or "")
                ),
                "",
            ),
            "next_expires_at": min(
                (
                    str(item.get("expires_at") or "")
                    for item in pending
                    if str(item.get("expires_at") or "")
                ),
                default="",
            ),
        }
    return result


def event_source_runtime_view(
    lineage_id: str,
    source_configs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return Event Source fire lifecycle independently from graph state."""

    runtime = read_runtime(lineage_id)
    states = runtime.get("event_sources") or {}
    result: dict[str, dict[str, Any]] = {}
    for raw_source_id, raw_config in source_configs.items():
        source_id = _item_id(raw_source_id, field="event_source_id")
        config = raw_config if isinstance(raw_config, dict) else {}
        source = states.get(source_id) or {}
        maximum = normalize_optional_limit(config.get("max_fires"))
        count = max(0, int(source.get("fire_count") or 0))
        exhausted = limit_reached(maximum, count)
        paused = bool(source.get("paused"))
        projection = {
            "state": "paused" if paused else "exhausted" if exhausted else "listening",
            "paused": paused,
            "max_fires": maximum,
            "occurrence_count": count,
            "remaining_fires": limit_remaining(maximum, count),
            "generation": max(
                0,
                int(source.get("fire_generation") or 0),
            ),
            "last_fired_at": str(source.get("last_fired_at") or ""),
            "last_delivery": deepcopy(source.get("last_delivery") or {}),
            "exhausted": exhausted,
        }
        if (
            str(config.get("event_type") or "") == "time.reached"
            and str(config.get("schedule_mode") or "") == "recurring"
        ):
            projection.update(
                {
                    "schedule_generation": max(
                        0,
                        int(source.get("generation") or 0),
                    ),
                    "last_scheduled_at": str(
                        source.get("last_scheduled_at") or ""
                    ),
                    "next_scheduled_at": str(
                        source.get("next_scheduled_at") or ""
                    ),
                    "next_due_at": str(
                        source.get("next_due_at")
                        or source.get("next_scheduled_at")
                        or ""
                    ),
                    "schedule_pending": bool(
                        source.get("pending_scheduled_at")
                    ),
                }
            )
        if (
            str(config.get("event_type") or "") == "time.reached"
            and str(config.get("schedule_mode") or "at") in {"at", "countdown"}
        ):
            # The one pending time of a one-shot or countdown source, until it fires.
            recognized = {*(source.get("consumed_cursors") or []), *(source.get("fired_cursors") or [])}
            if str(config.get("schedule_mode") or "at") == "countdown":
                upcoming = str(source.get("timer_due_at") or "")
                cursor = f"timer:{int(source.get('timer_generation') or 0)}:{upcoming}"
            else:
                upcoming = str(config.get("scheduled_at") or "")
                cursor = f"time:{upcoming}"
            if cursor in recognized or exhausted:
                upcoming = ""
            projection.update({"next_scheduled_at": upcoming, "next_due_at": upcoming})
        if str(config.get("event_type") or "") == "residents.all_idle":
            observed_value = str(source.get("state_value") or "")
            projection.update(
                {
                    "baseline_established": bool(observed_value),
                    "running_count": (
                        max(0, int(observed_value))
                        if observed_value
                        and observed_value.isdigit()
                        else None
                    ),
                }
            )
        if (
            str(config.get("event_type") or "")
            == "resident.context_pressure_exceeded"
        ):
            observed_value = str(source.get("state_value") or "")
            try:
                used_percent = (
                    round(float(observed_value), 1)
                    if observed_value
                    else None
                )
            except (TypeError, ValueError):
                used_percent = None
            try:
                threshold_percent = round(
                    float(
                        config.get("threshold_percent")
                        if config.get("threshold_percent") is not None
                        else 85
                    ),
                    1,
                )
            except (TypeError, ValueError):
                threshold_percent = 85.0
            projection.update(
                {
                    "baseline_established": used_percent is not None,
                    "used_percent": used_percent,
                    "threshold_percent": threshold_percent,
                    "exceeded": (
                        used_percent is not None
                        and used_percent > threshold_percent
                    ),
                }
            )
        event_type = str(config.get("event_type") or "")
        if event_type in {
            "resident.work_duration_exceeded",
            "resident.queue_pressure_exceeded",
            "state.condition_met",
        }:
            observed_value = str(source.get("state_value") or "")
            predicate_value = (
                observed_value == "true"
                if observed_value in {"true", "false"}
                else None
            )
            projection.update(
                {
                    "baseline_established": predicate_value is not None,
                    "condition_met": predicate_value,
                }
            )
            if event_type == "resident.work_duration_exceeded":
                projection["duration_seconds"] = max(
                    1,
                    int(config.get("duration_seconds") or 900),
                )
            elif event_type == "resident.queue_pressure_exceeded":
                metric = str(
                    config.get("pressure_metric") or "queue_length"
                )
                projection["pressure_metric"] = metric
                projection["pressure_threshold"] = max(
                    1,
                    int(
                        config.get("wait_seconds_threshold") or 300
                        if metric == "oldest_wait_seconds"
                        else config.get("queue_length_threshold") or 3
                    ),
                )
            else:
                projection["key"] = str(config.get("key") or "")
                projection["operator"] = str(
                    config.get("operator") or "equals"
                )
        result[source_id] = projection
    return result


def establish_resident_completion_subscription_baseline(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    subject_resident_id: str,
    message_id: str = "",
    completed_at: str = "",
    subscriber_ids: list[str] | tuple[str, ...] = (),
    per_resident: bool = False,
) -> dict[str, Any]:
    """Atomically fence completion history at subscription activation.

    A baseline is recognition evidence, not an Event occurrence: it must
    neither consume ``max_fires`` nor create a pending delivery.  With
    ``per_resident`` the completion cursor belongs to ``subject_resident_id``
    alone, for a source that watches several Residents.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(
        locator,
        field="Resident completion locator",
    )
    clean_subject_id = _item_id(
        subject_resident_id,
        field="Resident completion subject",
    )
    clean_message_id = str(message_id or "").strip()
    if clean_message_id:
        clean_message_id = _item_id(
            clean_message_id,
            field="Resident completion message id",
        )
    clean_completed_at = str(completed_at or "").strip()
    if clean_completed_at:
        clean_completed_at = _item_id(
            clean_completed_at,
            field="Resident completion timestamp",
        )
    subscribers = list(
        dict.fromkeys(
            _item_id(item, field="event subscriber")
            for item in subscriber_ids
        )
    )
    cursor = (
        f"message:{clean_subject_id}:{clean_message_id}"
        if clean_message_id
        else ""
    )
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        state = _observation_slot(source, clean_subject_id if per_resident else "", create=True)
        previous_locator = str(
            state.get("completion_locator") or ""
        )
        previous_message_id = str(
            state.get("completion_message_id") or ""
        )
        state["completion_locator"] = clean_locator
        state["completion_message_id"] = clean_message_id
        state["completion_generation"] = (
            max(
                0,
                int(state.get("completion_generation") or 0),
            )
            if previous_locator == clean_locator
            else 0
        )
        if clean_completed_at:
            state["completion_at"] = clean_completed_at
        else:
            state.pop("completion_at", None)

        occurrence_added = False
        claimed_subscriber_ids: list[str] = []
        if cursor:
            fired_cursors = list(source.get("fired_cursors") or [])
            if cursor not in fired_cursors:
                fired_cursors.append(cursor)
                occurrence_added = True
            source["fired_cursors"] = fired_cursors[
                -MAX_EVENT_FIRE_CURSORS:
            ]
            source.setdefault("fire_count", 0)
            generation = max(
                0,
                int(source.get("fire_generation") or 0),
            )
            source["fire_generation"] = generation

            consumed_cursors = list(
                source.get("consumed_cursors") or []
            )
            occupied = set(consumed_cursors)
            for subscriber_id in subscribers:
                if any(
                    _subscriber_cursor_matches_occurrence(
                        token,
                        cursor,
                        subscriber_id,
                    )
                    for token in occupied
                ):
                    continue
                token = _subscriber_cursor(
                    cursor,
                    subscriber_id,
                    generation,
                )
                consumed_cursors.append(token)
                occupied.add(token)
                claimed_subscriber_ids.append(subscriber_id)
            source["consumed_cursors"] = consumed_cursors[
                -MAX_EVENT_CURSORS:
            ]
        return {
            "status": "baseline",
            "event_source_id": source_id,
            "locator": clean_locator,
            "message_id": clean_message_id,
            "previous_message_id": previous_message_id,
            "cursor": cursor,
            "occurrence_added": occurrence_added,
            "claimed_subscriber_ids": claimed_subscriber_ids,
            "occurrence_count": max(0, int(source.get("fire_count") or 0)),
            "generation": max(
                0,
                int(source.get("fire_generation") or 0),
            ),
        }


def forget_event_source(
    lineage_id: str,
    event_source_id: str,
    *,
    preserve_pause: bool = False,
) -> None:
    source_id = _item_id(event_source_id, field="event_source_id")
    with _runtime_transaction(lineage_id) as runtime:
        previous = runtime["event_sources"].pop(source_id, None)
        if preserve_pause and isinstance(previous, dict) and previous.get("paused"):
            runtime["event_sources"][source_id] = {
                "consumed_cursors": [], "paused": True, "paused_at": previous.get("paused_at", ""),
            }


def fence_event_source_contract_preserving_fire_budget(
    lineage_id: str,
    event_source_id: str,
) -> None:
    """Fence stale subscriptions while retaining the source-wide fire count."""

    source_id = _item_id(event_source_id, field="event_source_id")
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if not isinstance(source, dict):
            return
        preserved = {
            key: deepcopy(source[key])
            for key in (
                "paused",
                "paused_at",
                "rebaseline",
                "fired_cursors",
                "fire_count",
                "fire_generation",
                "last_fired_at",
                "recurring_contract",
                "generation",
                "last_scheduled_at",
                "next_scheduled_at",
                "next_due_at",
                "next_missed_count",
                "pending_scheduled_at",
                "pending_due_at",
                "pending_next_scheduled_at",
                "pending_next_due_at",
                "pending_observed_at",
                "pending_missed_count",
                "pending_generation",
            )
            if key in source
        }
        if not preserved:
            runtime["event_sources"].pop(source_id, None)
        else:
            runtime["event_sources"][source_id] = {
                "consumed_cursors": [],
                **preserved,
            }


def observe_file_signature(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    signature: str,
) -> dict[str, Any]:
    """Compare one live file location with its last recognized signature.

    The first observation, and the first observation after the configured
    Resident/path changes, establishes a baseline. A later change is returned
    without advancing the baseline; callers commit it only after the Event is
    accepted for dispatch.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(locator, field="file locator")
    clean_signature = _item_id(signature, field="file signature")
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        previous_locator = str(source.get("file_locator") or "")
        previous_signature = str(source.get("file_signature") or "")
        if (
            previous_locator != clean_locator
            or not previous_signature
        ):
            source["consumed_cursors"] = []
            source["file_locator"] = clean_locator
            source["file_signature"] = clean_signature
            source["file_generation"] = 0
            return {
                "status": "baseline",
                "previous_signature": previous_signature,
                "signature": clean_signature,
                "generation": 0,
            }
        return {
            "status": (
                "unchanged"
                if previous_signature == clean_signature
                else "changed"
            ),
            "previous_signature": previous_signature,
            "signature": clean_signature,
            "generation": max(
                0,
                int(source.get("file_generation") or 0),
            ),
        }


def commit_file_signature(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    previous_signature: str,
    signature: str,
) -> bool:
    """CAS the file baseline after its change Event has been recognized."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(locator, field="file locator")
    clean_previous = _item_id(
        previous_signature,
        field="previous file signature",
    )
    clean_signature = _item_id(signature, field="file signature")
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if not isinstance(source, dict):
            return False
        if (
            str(source.get("file_locator") or "") != clean_locator
            or str(source.get("file_signature") or "")
            != clean_previous
        ):
            return False
        source["file_signature"] = clean_signature
        source["file_generation"] = max(
            0,
            int(source.get("file_generation") or 0),
        ) + 1
    return True


def _observation_slot(
    source: dict[str, Any],
    subject: str,
    *,
    create: bool,
) -> dict[str, Any] | None:
    """Where one observation lives: the source, or one Resident it watches among several."""

    if not subject:
        return source
    subjects = source.setdefault("subjects", {}) if create else source.get("subjects")
    if not isinstance(subjects, dict):
        return None
    state = subjects.setdefault(subject, {}) if create else subjects.get(subject)
    return state if isinstance(state, dict) else None


def _forget_resident_cursors(source: dict[str, Any], subject: str) -> None:
    """A watched Resident's new baseline frees only the occurrences recognized for it.

    Every occurrence key of a source that watches several Residents names its
    Resident, so the other Residents' recognized occurrences stay fenced.
    """

    source["consumed_cursors"] = [
        cursor for cursor in source.get("consumed_cursors") or [] if subject not in str(cursor)
    ]


def observation_state(
    runtime_sources: dict[str, Any],
    event_source_id: str,
    subject: str = "",
) -> dict[str, Any]:
    """Read-only view of one observation (see ``_observation_slot``)."""

    source = runtime_sources.get(event_source_id)
    if not isinstance(source, dict):
        return {}
    state = _observation_slot(source, subject, create=False)
    return state if isinstance(state, dict) else {}


def observe_event_source_value(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    signature: str,
    value: str,
    subject: str = "",
) -> dict[str, Any]:
    """Compare one live scalar value with its recognized Event baseline.

    The runtime retains only a locator, signature and small scalar value. It
    does not persist the observed object, Workspace content, or a replayable
    snapshot.  ``subject`` names one Resident of a source that watches
    several; each keeps its own baseline.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(locator, field="state locator")
    clean_signature = _item_id(signature, field="state signature")
    clean_value = _item_id(value, field="state value")
    clean_subject = _item_id(subject, field="watched Resident") if subject else ""
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        state = _observation_slot(source, clean_subject, create=True)
        previous_locator = str(state.get("state_locator") or "")
        previous_signature = str(state.get("state_signature") or "")
        previous_value = str(state.get("state_value") or "")
        if (
            previous_locator != clean_locator
            or not previous_signature
            or not previous_value
        ):
            if clean_subject:
                _forget_resident_cursors(source, clean_subject)
            else:
                source["consumed_cursors"] = []
            state["state_locator"] = clean_locator
            state["state_signature"] = clean_signature
            state["state_value"] = clean_value
            state["state_generation"] = 0
            state["state_epoch"] = secrets.token_hex(12)
            return {
                "status": "baseline",
                "resumed": bool(state.pop("rebaseline", False)),
                "previous_signature": previous_signature,
                "previous_value": previous_value,
                "signature": clean_signature,
                "value": clean_value,
                "generation": 0,
                "occurrence_key": state["state_epoch"] + ":1",
            }
        return {
            "status": (
                "unchanged"
                if previous_signature == clean_signature
                else "changed"
            ),
            "previous_signature": previous_signature,
            "previous_value": previous_value,
            "signature": clean_signature,
            "value": clean_value,
            "generation": max(
                0,
                int(state.get("state_generation") or 0),
            ),
            "occurrence_key": (
                (str(state["state_epoch"]) + ":" if state.get("state_epoch") else "")
                + str(max(0, int(state.get("state_generation") or 0)) + 1)
            ),
        }


def commit_event_source_value(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    previous_signature: str,
    signature: str,
    value: str,
    subject: str = "",
) -> bool:
    """CAS one scalar Event baseline after its change is recognized."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(locator, field="state locator")
    clean_previous = _item_id(
        previous_signature,
        field="previous state signature",
    )
    clean_signature = _item_id(signature, field="state signature")
    clean_value = _item_id(value, field="state value")
    clean_subject = _item_id(subject, field="watched Resident") if subject else ""
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if not isinstance(source, dict):
            return False
        state = _observation_slot(source, clean_subject, create=False)
        if state is None:
            return False
        if (
            str(state.get("state_locator") or "") != clean_locator
            or str(state.get("state_signature") or "")
            != clean_previous
        ):
            return False
        state["state_signature"] = clean_signature
        state["state_value"] = clean_value
        state["state_generation"] = max(
            0,
            int(state.get("state_generation") or 0),
        ) + 1
        # Legacy baselines retain their pending occurrence ID until it is
        # recognized. Subsequent transitions move to a fresh identity namespace.
        state.setdefault("state_epoch", secrets.token_hex(12))
    return True


def observe_resident_completion(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    message_id: str = "",
    completed_at: str = "",
    subject: str = "",
) -> dict[str, Any]:
    """Compare one completed Resident input with this source's live cursor.

    The first observation establishes a baseline, including the explicit
    empty baseline used when the Resident has never completed an input. This is
    deliberately not an Event log: only the latest recognized Message id is kept,
    and a newly authored Watch cannot replay an older completion.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(
        locator,
        field="Resident completion locator",
    )
    clean_message_id = str(message_id or "").strip()
    if clean_message_id:
        clean_message_id = _item_id(
            clean_message_id,
            field="Resident completion message id",
        )
    clean_completed_at = str(completed_at or "").strip()
    if clean_completed_at:
        clean_completed_at = _item_id(
            clean_completed_at,
            field="Resident completion timestamp",
        )
    clean_subject = _item_id(subject, field="watched Resident") if subject else ""
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        state = _observation_slot(source, clean_subject, create=True)
        previous_locator = str(
            state.get("completion_locator") or ""
        )
        previous_message_id = str(
            state.get("completion_message_id") or ""
        )
        previous_completed_at = str(
            state.get("completion_at") or ""
        )
        generation = max(
            0,
            int(state.get("completion_generation") or 0),
        )
        if previous_locator != clean_locator:
            state["completion_locator"] = clean_locator
            state["completion_message_id"] = clean_message_id
            state["completion_generation"] = 0
            if clean_completed_at:
                state["completion_at"] = clean_completed_at
            else:
                state.pop("completion_at", None)
            return {
                "status": "baseline",
                "previous_message_id": previous_message_id,
                "message_id": clean_message_id,
                "previous_completed_at": previous_completed_at,
                "completed_at": clean_completed_at,
                "generation": 0,
            }
        return {
            "status": (
                "unchanged"
                if not clean_message_id or clean_message_id == previous_message_id
                else "changed"
            ),
            "previous_message_id": previous_message_id,
            "message_id": clean_message_id,
            "previous_completed_at": previous_completed_at,
            "completed_at": clean_completed_at,
            "generation": generation,
        }


def commit_resident_completion(
    lineage_id: str,
    event_source_id: str,
    *,
    locator: str,
    previous_message_id: str = "",
    message_id: str,
    completed_at: str = "",
    subject: str = "",
) -> bool:
    """CAS-advance a Resident completion cursor after Event recognition."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_locator = _item_id(
        locator,
        field="Resident completion locator",
    )
    clean_previous_message_id = str(previous_message_id or "").strip()
    if clean_previous_message_id:
        clean_previous_message_id = _item_id(
            clean_previous_message_id,
            field="previous Resident completion message id",
        )
    clean_message_id = _item_id(
        message_id,
        field="Resident completion message id",
    )
    clean_completed_at = str(completed_at or "").strip()
    if clean_completed_at:
        clean_completed_at = _item_id(
            clean_completed_at,
            field="Resident completion timestamp",
        )
    clean_subject = _item_id(subject, field="watched Resident") if subject else ""
    with _runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].get(source_id)
        if not isinstance(source, dict):
            return False
        state = _observation_slot(source, clean_subject, create=False)
        if state is None:
            return False
        if (
            str(state.get("completion_locator") or "")
            != clean_locator
            or str(state.get("completion_message_id") or "")
            != clean_previous_message_id
        ):
            return False
        state["completion_message_id"] = clean_message_id
        if clean_completed_at:
            state["completion_at"] = clean_completed_at
        else:
            state.pop("completion_at", None)
        state["completion_generation"] = max(
            0,
            int(state.get("completion_generation") or 0),
        ) + 1
    return True


def register_resident_handoff(lineage_id: str, *, message_id: str, resident_id: str,
                                  trigger_path_id: str, event: dict[str, Any], source_wire_id: str,
                                  queued_at: str = "") -> dict[str, Any]:
    return trigger_paths.admit_resident(lineage_id, message_id=message_id, resident_id=resident_id,
        trigger_path_id=trigger_path_id, event=event, source_wire_id=source_wire_id, queued_at=queued_at)



def settle_resident_handoffs(lineage_id: str, *, message_id: str, resident_id: str,
                                status: str, completed_at: str = "", output: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = trigger_paths.settle_input(lineage_id, resident_id, message_id, status=status,
        at=completed_at or _iso_now(), execution_id=str((output or {}).get("execution_id") or ""))
    with _runtime_transaction(lineage_id) as runtime:
        runtime["delivery_profiles"].pop(message_id, None)
    return rows



def pending_resident_handoffs(lineage_id: str) -> list[dict[str, Any]]:
    return trigger_paths.resident_inputs(lineage_id, pending=True)



def resident_handoff(lineage_id: str, resident_id: str, message_id: str) -> dict[str, Any] | None:
    return trigger_paths.resident_input(lineage_id, resident_id, message_id)



def path_resident_inputs(lineage_id: str, trigger_path_id: str) -> list[dict[str, Any]]:
    return trigger_paths.resident_inputs(lineage_id, paths=[trigger_path_id])



def settle_trigger_path(lineage_id: str, trigger_path_id: str) -> bool:
    finished = trigger_paths.settle(trigger_path_id)
    if finished:
        from .changes import lineage_changed
        lineage_changed(lineage_id, runtime=True)
    return finished



def _stored_normalization(connection: Any) -> int:
    row = connection.execute("SELECT value FROM lineage_meta WHERE key=%s", (_NORMALIZATION_KEY,)).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _reads_trusted(connection: Any) -> bool:
    """Whether this rule normalized every stored object; checked again once a minute."""

    global _trust
    checked_at, trusted = _trust
    now = time.monotonic()
    if now - checked_at >= _TRUST_SECONDS:
        trusted = _stored_normalization(connection) == RUNTIME_NORMALIZATION
        _trust = (now, trusted)
    return trusted


def migrate_stored_runtime() -> dict[str, Any]:
    """Renormalize every stored object once after RUNTIME_NORMALIZATION changed.

    Each Lineage is one short transaction that writes back only the objects
    this rule changes. The stored number moves only after all of them, so
    reads keep normalizing until the migration has finished.
    """

    database = get_lineage_data_store().database
    with database.read_current() as connection:
        if _stored_normalization(connection) == RUNTIME_NORMALIZATION:
            return {"normalization": RUNTIME_NORMALIZATION, "lineages": 0, "migrated": 0}
        lineage_ids = sorted({row["lineage_id"] for row in connection.execute(
            " UNION ".join(f"SELECT lineage_id FROM {table}" for table in program_work_storage.COLUMNS),
        ).fetchall()})
    migrated = 0
    for lineage_id in lineage_ids:
        with program_work_storage.transaction(database, lineage_id) as runtime:
            # Touch the fields in lock order, as every runtime writer does:
            # the expiry notices come between the wires and the operators.
            for field in program_work_storage.LOCK_ORDER:
                if field == "expired_deliveries":
                    expired = runtime[field]
                    value = _normalize_object(field, "", expired) if expired else []
                    if value != expired:
                        migrated += 1
                        runtime[field] = value
                    continue
                objects = runtime[field]
                for key in list(objects):
                    value = _normalize_object(field, key, objects[key])
                    if value != objects[key]:
                        migrated += 1
                        if value is None:
                            del objects[key]
                        else:
                            objects[key] = value
    database.write(lambda connection: connection.execute(
        "INSERT INTO lineage_meta(key, value) VALUES (%s, %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_NORMALIZATION_KEY, str(RUNTIME_NORMALIZATION)),
    ))
    return {"normalization": RUNTIME_NORMALIZATION, "lineages": len(lineage_ids), "migrated": migrated}


def read_runtime(lineage_id: str) -> dict[str, Any]:
    clean_id = _item_id(lineage_id, field="lineage_id")
    with get_lineage_data_store().database.read_current() as connection:
        stored = program_work_storage.read(connection, clean_id)
        if _reads_trusted(connection):
            return {**empty_runtime(), **stored}
    return _normalize(stored)


def _read_object(lineage_id: str, field: str, key: str) -> Any:
    """One normalized source, operator or delivery profile, read on its own."""

    clean_id = _item_id(lineage_id, field="lineage_id")
    with get_lineage_data_store().database.read_current() as connection:
        stored = program_work_storage.read_object(connection, clean_id, field, key)
        if stored is None or _reads_trusted(connection):
            return stored
    return _normalize_object(field, key, stored)


def read_operator_state(lineage_id: str, operator_id: str) -> dict[str, Any]:
    """Read one participant's state from the Program-owned runtime."""

    clean_id = _item_id(operator_id, field="Program operator id")
    return _read_object(lineage_id, "operators", clean_id) or {}


@contextmanager
def operator_state_transaction(
    lineage_id: str,
    operator_id: str,
) -> Iterator[dict[str, Any]]:
    """Atomically mutate one participant without exposing a new namespace."""

    clean_id = _item_id(operator_id, field="Program operator id")
    with _runtime_transaction(lineage_id) as runtime:
        state = runtime["operators"].setdefault(clean_id, {})
        yield state
        if not state:
            runtime["operators"].pop(clean_id, None)


def delete_operator_state(lineage_id: str, operator_id: str) -> None:
    clean_id = _item_id(operator_id, field="Program operator id")
    with _runtime_transaction(lineage_id) as runtime:
        runtime["operators"].pop(clean_id, None)


def migrate_objects(source_id: str, target_id: str, object_ids: set[str], *, dropped_wire_ids: set[str]) -> None:
    """Move cursors, budgets, latest results and queued work with moved objects.

    Moved objects need not be idle: an Event Source keeps its cursor and
    budget, an Effect its latest result, an operator its state, and an internal
    wire its queued deliveries, so consumed facts are never replayed. Queued
    deliveries on detached wires are deleted with the wire. Resident
    invocations and Run history stay in the originating Lineage. Caller holds
    both execution authorities and the shared state transaction.
    """
    with _runtime_transaction(source_id) as source, _runtime_transaction(target_id) as target:
        from . import wire_delivery_budget
        wire_delivery_budget.forget(source_id, dropped_wire_ids)
        wire_delivery_budget.move(source_id, target_id, object_ids)
        for wire_id in dropped_wire_ids:
            source["event_wires"].pop(wire_id, None)
        for field in ("event_sources", "event_wires", "operators"):
            for object_id in object_ids & source[field].keys():
                if object_id in target[field]:
                    raise ValueError("目标 Lineage 已存在相同 ID 的运行状态：" + object_id)
                target[field][object_id] = source[field].pop(object_id)

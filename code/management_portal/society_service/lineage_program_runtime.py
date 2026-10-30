"""The stored Program runtime of a Lineage: its shape, the normalization rule every stored
object passes, transactions and reads, the migration between rule versions, the
participants' state maps and the runtime clock.

The work on it lives with what it is about: occurrences (``lineage_program_occurrences``),
time sources (``lineage_program_schedules``), queued wire deliveries
(``lineage_program_wire_queue``), observation baselines (``lineage_program_observations``),
Effect results (``lineage_program_effect_results``) and Resident inputs
(``lineage_program_resident_inputs``).
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterator

from . import lineage_primitives, program_work_storage
from .state_store import get_lineage_data_store


RUNTIME_SCHEMA = "tfi.hatg-program-runtime.v4"
MAX_EVENT_CURSORS = 256
MAX_EVENT_FIRE_CURSORS = 256
# An Event Source watching several Residents keeps one observation per Resident.
MAX_WATCHED_RESIDENTS = 200
TERMINAL_INVOCATION_STATUSES = frozenset({"completed", "failed", "cancelled"})
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


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _utc_now() -> datetime:
    """The runtime clock: every Program runtime module reads the time here."""

    return datetime.now(timezone.utc)


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

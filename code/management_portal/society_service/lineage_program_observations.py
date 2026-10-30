"""Observation baselines of Event Sources: file signatures, scalar state values and
Resident completions, kept per source or per watched Resident and advanced by CAS.
"""

from __future__ import annotations

import secrets
from typing import Any

from . import lineage_program_runtime
from .lineage_program_occurrences import _subscriber_cursor, _subscriber_cursor_matches_occurrence
from .lineage_program_runtime import MAX_EVENT_CURSORS, MAX_EVENT_FIRE_CURSORS, _item_id


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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
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

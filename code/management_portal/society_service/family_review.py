"""Activity-gated organizational review; Family owns watermarks and dispatch intent."""
from __future__ import annotations

import copy
import hashlib
import json

from management_common.message_identity import derived_message_identity

from . import invariant_locks, lineage_aggregate, resident_conversation_stream, resident_held_infos
from . import resident_record_domain, resident_protocol
from .family_familize import read_family
from .family_task_dispatches import FamilyTaskTargetChanged, accepted_receipt, dispatch
from .state_store import get_lineage_data_store

REF = "family-activity-review"


def _ledger(relay):
    ledger = relay.setdefault("activity_review", {"activity": {}, "reviewed": {}, "pending": None,
                                                "position_format": "conversation_cursor"})
    if ledger.get("position_format") != "conversation_cursor":
        # Convert only already recorded references. No historical activity is
        # discovered, and a pending request keeps its immutable original facts.
        repo = resident_conversation_stream.get_repository()
        for field in ("activity", "reviewed"):
            for rid, position in ledger[field].items():
                cursor = repo.completed_input_cursor(rid, position[1])
                if cursor is None:
                    raise ValueError("Family review completion receipt is unavailable")
                ledger[field][rid] = [cursor, position[1]]
        ledger["position_format"] = "conversation_cursor"
    return ledger


def _consume(ledger, pending):
    repo = resident_conversation_stream.get_repository()
    for rid, value in pending["activity"].items():
        # Pending may predate the cursor format. Never rewrite its facts/ID.
        cursor = repo.completed_input_cursor(rid, value[1])
        if cursor is None:
            raise ValueError("Family review completion receipt is unavailable")
        position = [cursor, value[1]]
        ledger["reviewed"][rid] = max(ledger["reviewed"].get(rid, position), position)
    ledger["pending"] = None


def _completed_position(family, event):
    """Require persisted completion and use its first Conversation event position."""
    if not isinstance(event, dict) or event.get("type") != "resident.work_finished":
        return None
    rid = event.get("subject_resident_id")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    mid = payload.get("message_id")
    if rid not in family["members"] or not mid:
        return None
    repo = resident_conversation_stream.get_repository()
    try:
        state = repo.input_state(rid, mid)
    except KeyError:
        return None
    message = repo.message(rid, mid) or {}
    if (state.get("state") != "completed" or message.get("role") != "user"
            or repo.message_business_ref(rid, mid).startswith("duty/")
            or resident_held_infos.is_info({"resident_id": rid}, mid)):
        return None
    cursor = repo.completed_input_cursor(rid, mid)
    return (rid, [cursor, mid]) if cursor is not None else None


def _idle(steward):
    snapshots = resident_protocol.read_resident_state_snapshots([steward])
    state = (snapshots.get(steward) or {}).get("state") or {}
    return state.get("work_state") == "idle" and state.get("queued_count") == 0


def check(lineage_id, family_id, actor, *, event=None):
    body, block = read_family(lineage_id, family_id, actor)
    family = block["family"]
    if actor not in {family["facade"], family["steward"]}:
        lineage_aggregate.ensure_manager(body, actor)
    if family.get("personal_ama_id"):
        return {"family_id": block["id"], "status": "not_applicable"}
    facade, steward = family["facade"], family["steward"]
    store = get_lineage_data_store().family_relays
    if event is not None:
        completed = _completed_position(family, event)
        if completed is None:
            return {"family_id": block["id"], "status": "skipped"}
        rid, position = completed
        with store.transaction(lineage_id, facade) as relay:
            activity = _ledger(relay)["activity"]
            activity[rid] = max(activity.get(rid, position), position)
        return {"family_id": block["id"], "status": "recorded"}

    # Serialize check/dispatch, but release the ledger transaction before any
    # state read or message admission. Completion recording can proceed meanwhile.
    with invariant_locks.family_review_lock(facade) as owned:
        if not owned:
            return {"family_id": block["id"], "status": "busy"}
        record = resident_record_domain.find_resident(facade)
        if not record or resident_record_domain.resident_intake_state(record) != "open":
            return {"family_id": block["id"], "status": "paused"}
        with store.transaction(lineage_id, facade) as relay:
            ledger = _ledger(relay)
            # Membership is checked when recording. Later roster changes must
            # not discard work legitimately recorded before a member left.
            snapshot = copy.deepcopy(ledger["activity"])
            pending = copy.deepcopy(ledger["pending"])
            changed = any(value > ledger["reviewed"].get(rid, []) for rid, value in snapshot.items())
        if not pending and not changed:
            return {"family_id": block["id"], "status": "unchanged"}
        # A receipt after a crash permits finishing the same intent even while
        # its Steward is busy; replaying dispatch never reopens that input.
        accepted = None
        if pending:
            accepted = accepted_receipt(facade, pending["steward"], "review", pending["message_id"],
                                        family_id=block["id"], facts={"activity_versions": pending["activity"]})
        if not accepted and pending and pending["steward"] != steward:
            return {"family_id": block["id"], "status": "target_changed"}
        if not accepted and not _idle(steward):
            return {"family_id": block["id"], "status": "busy"}
        if not pending:
            digest = hashlib.sha256(json.dumps([steward, snapshot], sort_keys=True).encode()).hexdigest()
            pending = {"steward": steward, "activity": snapshot,
                       "message_id": derived_message_identity(REF, facade, digest)}
            with store.transaction(lineage_id, facade) as relay:
                _ledger(relay)["pending"] = copy.deepcopy(pending)
        try:
            result = accepted or dispatch(facade, "review", facade, message_id=pending["message_id"],
                                         expected_target_id=pending["steward"],
                                         facts={"activity_versions": pending["activity"]})
        except FamilyTaskTargetChanged:
            return {"family_id": block["id"], "status": "target_changed"}
        with store.transaction(lineage_id, facade) as relay:
            ledger = _ledger(relay)
            _consume(ledger, pending)
        return {"family_id": block["id"], "status": "requested", "message_id": result["message_id"]}

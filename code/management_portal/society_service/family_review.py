"""Activity-gated organizational review; Family owns watermarks and dispatch intent."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime

from management_common.message_identity import derived_message_identity

from . import invariant_locks, lineage_aggregate, resident_conversation_stream, resident_held_infos
from . import resident_record_domain, resident_protocol
from .family_familize import read_family
from .family_task_dispatches import dispatch
from .state_store import get_lineage_data_store

REF = "family-activity-review"


def _ledger(relay):
    return relay.setdefault("activity_review", {"activity": {}, "reviewed": {}, "pending": None})


def _completed_position(family, event):
    """Use the immutable input's persisted terminal receipt, never event timestamps."""
    if not isinstance(event, dict) or event.get("type") != "resident.work_finished":
        return None
    rid = event.get("subject_resident_id")
    mid = (event.get("payload") or {}).get("message_id")
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
    # Each member's latest completion covers earlier work even when event
    # deliveries arrive late. Replays cannot move the watermark backwards.
    return rid, [datetime.fromisoformat(state["updated_at"].replace("Z", "+00:00")).timestamp(), mid]


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
            snapshot = {rid: value for rid, value in ledger["activity"].items() if rid in family["members"]}
            pending = copy.deepcopy(ledger["pending"])
            changed = any(value > ledger["reviewed"].get(rid, []) for rid, value in snapshot.items())
        if not pending and not changed:
            return {"family_id": block["id"], "status": "unchanged"}
        repo = resident_conversation_stream.get_repository()
        # A receipt after a crash permits finishing the same intent even while
        # its Steward is busy; replaying dispatch never reopens that input.
        accepted = None
        if pending:
            try:
                accepted = repo.input_state(pending["steward"], pending["message_id"])
            except KeyError:
                pass
        if pending and pending["steward"] != steward:
            return {"family_id": block["id"], "status": "target_changed"}
        if not accepted and not _idle(steward):
            return {"family_id": block["id"], "status": "busy"}
        if not pending:
            digest = hashlib.sha256(json.dumps([steward, snapshot], sort_keys=True).encode()).hexdigest()
            pending = {"steward": steward, "activity": snapshot,
                       "message_id": derived_message_identity(REF, facade, digest)}
            with store.transaction(lineage_id, facade) as relay:
                _ledger(relay)["pending"] = copy.deepcopy(pending)
        result = dispatch(facade, "review", facade, message_id=pending["message_id"],
                          facts={"activity_versions": pending["activity"]})
        with store.transaction(lineage_id, facade) as relay:
            ledger = _ledger(relay)
            for rid, value in pending["activity"].items():
                ledger["reviewed"][rid] = max(ledger["reviewed"].get(rid, value), value)
            ledger["pending"] = None
        return {"family_id": block["id"], "status": "requested", "message_id": result["message_id"]}

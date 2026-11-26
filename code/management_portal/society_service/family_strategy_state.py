"""Move active Family policy facts into Lineage Shared State at cutover."""
from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import Callable

from management_common.json_columns import json_text, json_value

from . import family_review, family_task_dispatches, isa_migration_scope, lineage_graph_state, lineage_primitives
from .resident_conversation_records import INPUT_STATE_CHANGED


def _records(document, key):
    value = document.get(key, {})
    if not isinstance(value, dict) or any(not isinstance(item, dict) for item in value.values()):
        raise ValueError(f"Malformed Family policy records: {key}")
    return value


def _recovery_cell(supervision, input_status):
    incidents = _records(supervision, "incidents")
    notice = supervision.get("notice") or {}
    if not isinstance(notice, dict):
        raise ValueError("Malformed Family recovery notice")
    if notice:
        status = input_status(notice["message_id"])
        # A persisted input may have been accepted just before the policy
        # ledger committed its delivery flag.
        if status in {"completed", "failed", "cancelled"} or notice.get("withdrawn"):
            notice = {}
        elif notice.get("delivered") and status is None:
            raise ValueError("Missing Family recovery notice receipt")
    keep = set(notice.get("incident_keys", []))
    if not keep.issubset(incidents):
        raise ValueError("Family recovery notice refers to an unknown incident")
    active = {key: item for key, item in incidents.items()
              if key in keep or item.get("status") not in {"resolved", "cancelled", "escalated"}}
    if not active and not notice:
        return None
    head = None
    linked = {}
    for key, item in sorted(active.items(), key=lambda pair: (pair[1].get("created_at", ""), pair[0])):
        linked[key] = {**item, "previous": head}
        head = key
    return {"head": head, "incidents": linked, **({"notice": notice} if notice else {})}


def _routing_cell(reviews, input_status):
    keep = {}
    for name in ("requests", "scope_requests"):
        records = _records(reviews, name)
        pending = {}
        for msg, entry in records.items():
            if entry.get("status") in {"relayed", "withdrawn"}:
                continue
            status = input_status(msg)
            if status is None:
                raise ValueError("Missing Family routing request receipt")
            if status not in {"completed", "cancelled"}:
                pending[msg] = entry
        if pending:
            keep[name] = pending
    return keep or None


def plan_cells(family_id, relay, reviews, *, completed_cursor: Callable, root_status: Callable,
               steward_status: Callable) -> dict:
    """Select active policy facts without changing either legacy store."""
    if any(not isinstance(source, dict) for source in (relay, reviews)):
        raise ValueError("Malformed Family policy document")
    if any(key in relay and not isinstance(relay[key], dict)
           for key in ("activity_review", "progress", "supervision")):
        raise ValueError("Malformed Family policy ledger")
    result = {}
    if ledger := relay.get("activity_review"):
        result["review"] = family_review.migrate_activity_review(ledger, completed_cursor)
    if progress := relay.get("progress"):
        batches = _records(progress, "batches")
        waiting = {key: batch for key, batch in batches.items() if batch.get("status") == "pending"}
        if waiting:
            result["progress"] = {
                "count": progress["count"],
                "reported_through": progress["reported_through"],
                "batches": waiting,
            }
    if supervision := relay.get("supervision"):
        if recovery := _recovery_cell(supervision, steward_status):
            result["recovery"] = recovery
    if routing := _routing_cell(reviews, root_status):
        result["routing"] = routing
    return {f"family.{family_id}.{domain}": deepcopy(value) for domain, value in result.items()}


def _cursor(connection, resident_id, message_id):
    row = connection.execute(
        "SELECT cursor FROM conversation_events WHERE resident_id=%s AND subject_id=%s "
        "AND event_type=%s AND payload_json->'input_state'->>'state'='completed' ORDER BY cursor LIMIT 1",
        (resident_id, message_id, INPUT_STATE_CHANGED),
    ).fetchone()
    return int(row["cursor"]) if row else None


def _input_status(connection, resident_id, message_id):
    row = connection.execute(
        "SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s",
        (resident_id, message_id),
    ).fetchone()
    return row["state"] if row else None


def _family_cells(connection, lineage_id):
    """Plan from the caller's snapshot without initialization or mutation."""
    rows = connection.execute(
        """SELECT f.family_id, facade.resident_id AS facade_id, steward.resident_id AS steward_id,
                  relays.relay_json, quota.review_json
             FROM families f
             JOIN family_residents facade ON facade.family_id=f.family_id AND facade.role='facade'
             JOIN family_residents steward ON steward.family_id=f.family_id AND steward.role='steward'
        LEFT JOIN family_relays relays ON relays.lineage_id=f.lineage_id AND relays.resident_id=facade.resident_id
        LEFT JOIN family_quota_reviews quota ON quota.lineage_id=f.lineage_id AND quota.resident_id=facade.resident_id
            WHERE f.lineage_id=%s AND f.deleted_at IS NULL ORDER BY f.family_id""",
        (lineage_id,),
    ).fetchall()
    candidates = {}
    for row in rows:
        candidates.update(plan_cells(
            row["family_id"], json_value(row["relay_json"]) if row["relay_json"] else {},
            json_value(row["review_json"]) if row["review_json"] else {},
            completed_cursor=lambda resident_id, message_id: _cursor(connection, resident_id, message_id),
            root_status=lambda message_id: _input_status(connection, row["facade_id"], message_id),
            steward_status=lambda message_id: _input_status(connection, row["steward_id"], message_id),
        ))
    return candidates


def migrate_family_state(connection, lineage_id):
    """Copy verified facts in the caller's stopped-owner graph transaction."""
    return _write_cells(connection, lineage_id, _family_cells(connection, lineage_id))


def check_family_state(connection, lineage_id):
    """Validate the same merge and capacity within a read-only snapshot."""
    try:
        candidates = _family_cells(connection, lineage_id)
        if candidates:
            stored = connection.execute(
                "SELECT cells_json FROM lineage_shared_state WHERE lineage_id=%s", (lineage_id,),
            ).fetchone()
            _merge_cells(json_value(stored["cells_json"]) if stored else {}, candidates)
    except ValueError as error:
        return [{"kind": "family_state", "lineage_id": lineage_id, "reason": str(error)}]
    return []


def _write_cells(connection, lineage_id, candidates):
    if not candidates:
        return []
    stored = connection.execute(
        "SELECT cells_json FROM lineage_shared_state WHERE lineage_id=%s FOR UPDATE", (lineage_id,),
    ).fetchone()
    state, changed = _merge_cells(json_value(stored["cells_json"]) if stored else {}, candidates)
    if changed:
        connection.execute(
            "INSERT INTO lineage_shared_state (lineage_id,cells_json) VALUES (%s,%s) "
            "ON CONFLICT(lineage_id) DO UPDATE SET cells_json=excluded.cells_json",
            (lineage_id, json_text(state, ensure_ascii=False, sort_keys=True)),
        )
    return changed


def _merge_cells(raw, candidates):
    state = lineage_graph_state._normalize(raw)
    changed = []
    for key, value in candidates.items():
        if key in state["cells"]:
            if not _same_cell(state["cells"][key], value):
                raise ValueError(f"Family policy migration conflicts with Shared State cell {key}")
        else:
            state["cells"][key] = value
            changed.append(key)
    if changed:
        state["revision"] += 1
        state["updated_at"] = lineage_primitives.now_iso()
        state = lineage_graph_state._normalize(state)
    return state, changed


def _same_cell(left, right):
    return json_text(left, sort_keys=True) == json_text(right, sort_keys=True)


def _legacy_dispatch_scope(connection):
    rows = connection.execute("""
        SELECT DISTINCT t.lineage_id, (l.lineage_id IS NOT NULL AND l.deleted_at IS NULL) AS live
        FROM family_task_dispatches t LEFT JOIN lineages l USING(lineage_id)
        ORDER BY t.lineage_id
    """).fetchall()
    active = []
    retain_table = False
    for row in rows:
        lineage_id = row["lineage_id"]
        if isa_migration_scope.is_deferred(lineage_id):
            retain_table = True
        elif row["live"]:
            active.append(lineage_id)
    return active, retain_table


# One reviewed production decision, recorded by GetConversation from weihao's
# original User input. This is migration data, not a caller-controlled override.
_AUTHORIZED_ABANDONMENT = {
    "lineage_id": "lineage_8794980b1bb7",
    "message_id": "msg_dabab17f8068d5d9f7440b7a9060001f",
    "intent_sha256": "60f45291f787c04836791979abbf362ebefde2ef33a3d17035a1e5ee62095787",
    "facade_id": "resident_94b20c757fe7",
    "target_id": "resident_6b4867560621",
    "initiated_by": "resident_dfc7781a9baa",
    "authorization": {
        "resident_id": "resident_9edb49ed68fb",
        "message_id": "msg_37a7163b7eaa8ccf2c44fa5327a550d1",
        "user_id": "weihao",
        "occurred_at": "2026-11-26T18:47:09Z",
        "content_sha256": "9c1d0388f77d134d250818ea01549b5b80a1768272b68677ecd8f73966355ef0",
        "instruction_id": "inst_lin_2650a12d66587d9a4067",
        "receipt_sha256": "d629460932e6d6ee734a410a2eaa265f0246856de5c9c75ed13b9d0c95d22669",
    },
}


def _dispatch_table_exists(connection, name):
    row = connection.execute(
        "SELECT to_regclass(quote_ident(current_schema()) || '.' || quote_ident(%s)) AS name", (name,),
    ).fetchone()
    return bool(row and row["name"])


def _fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _read_authorization_message(connection, evidence):
    from management_common.portal_database import _database_schema
    from management_portal.execution.agent_data_chat import ChatReads
    from .resident_conversation_records import message_from_storage, protocol_message

    class SnapshotChat(ChatReads):
        def _bring_current(self, sequence_ids):
            if sequence_ids:
                raise family_task_dispatches._DispatchProjectionBehind

    chat = SnapshotChat(family_task_dispatches._DispatchSnapshot(connection, schema=_database_schema("agent")))
    stored = chat.chat_message(evidence["resident_id"], evidence["message_id"])
    return protocol_message(message_from_storage(stored)) if stored is not None else None


def _same_archived_dispatch(connection, lineage_id, message_id, intent, decision):
    prior = connection.execute("SELECT intent_json, decision_json FROM family_dispatch_dispositions "
                               "WHERE lineage_id=%s AND message_id=%s", (lineage_id, message_id)).fetchone()
    if prior is None:
        return None
    return _same_cell(prior["intent_json"], intent) and _same_cell(prior["decision_json"], decision)


def _plan_abandonment(connection, lineage_id, message_id, reason):
    expected = _AUTHORIZED_ABANDONMENT
    if (lineage_id, message_id, reason) != (expected["lineage_id"], expected["message_id"], "unaccepted_frozen_intent"):
        return None, reason
    row = connection.execute("SELECT intent_json FROM family_task_dispatches WHERE lineage_id=%s AND message_id=%s",
                             (lineage_id, message_id)).fetchone()
    if row is None:
        return None, reason
    intent = row["intent_json"]
    if (_fingerprint(intent) != expected["intent_sha256"] or
            intent["request"]["facade"] != expected["facade_id"] or
            intent["request"]["target"] != expected["target_id"] or
            intent["initiated_by"] != expected["initiated_by"]):
        return None, reason

    evidence = expected["authorization"]
    try:
        approval = _read_authorization_message(connection, evidence)
    except (family_task_dispatches._DispatchProjectionBehind, ValueError):
        approval = None
    user = {"kind": "user", "id": evidence["user_id"]}
    if (not approval or approval["role"] != "user" or approval["message_id"] != evidence["message_id"] or
            approval.get("author_id") != user["id"] or (approval.get("origin") or {}).get("sender") != user or
            (approval.get("origin") or {}).get("initiated_by") != user or
            _fingerprint(approval["content_parts"]) != evidence["content_sha256"]):
        return None, "abandonment_authorization_unverified"

    decision = {**expected, "disposition": "abandoned_by_user", "admission": "unknown"}
    if (_dispatch_table_exists(connection, "family_dispatch_dispositions") and
            _same_archived_dispatch(connection, lineage_id, message_id, intent, decision) is False):
        return None, "conflicting_dispatch_disposition"
    return (intent, decision), None


def _archive_dispatch_dispositions(connection, lineage_id, dispositions):
    if not dispositions:
        return
    # This is an internal audit archive, separate from public Shared State and
    # from routing receipts. The original intent and decision survive retirement.
    connection.execute("""CREATE TABLE IF NOT EXISTS family_dispatch_dispositions (
        lineage_id TEXT NOT NULL, message_id TEXT NOT NULL, intent_json JSONB NOT NULL,
        decision_json JSONB NOT NULL, PRIMARY KEY (lineage_id, message_id))""")
    for message_id, (intent, decision) in dispositions.items():
        inserted = connection.execute("""INSERT INTO family_dispatch_dispositions
            (lineage_id, message_id, intent_json, decision_json) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (lineage_id, message_id, json_text(intent), json_text(decision)))
        if not inserted.rowcount and not _same_archived_dispatch(connection, lineage_id, message_id, intent, decision):
            raise ValueError(f"Cannot retire Family task {message_id}: conflicting_dispatch_disposition")


def check_legacy_dispatches(connection):
    """Check live legacy intents on the caller's snapshot without initializing stores."""
    if not _dispatch_table_exists(connection, "family_task_dispatches"):
        return []
    lineages, _retain_table = _legacy_dispatch_scope(connection)
    if not lineages:
        return []
    readers = family_task_dispatches.snapshot_dispatch_readers(connection)
    issues = []
    for lineage_id in lineages:
        candidates, blockers, _dispositions = _dispatch_candidates(connection, lineage_id, readers)
        if candidates:
            stored = connection.execute("SELECT cells_json FROM lineage_shared_state WHERE lineage_id=%s",
                                        (lineage_id,)).fetchone()
            cells = lineage_graph_state._normalize(json_value(stored["cells_json"]) if stored else {})["cells"]
            for key, value in candidates.items():
                if key in cells and not _same_cell(cells[key], value):
                    blockers.extend((message_id, "conflicting_shared_state_receipt") for message_id in value)
        issues.extend({"kind": "legacy_family_dispatch", "lineage_id": lineage_id,
                       "message_id": message_id, "reason": reason} for message_id, reason in blockers)
    return issues


def retire_legacy_dispatches() -> None:
    """Replace the stopped owner's frozen intents with proven unfinished receipts."""
    from .resident_conversation_stream import get_repository
    from .state_store import get_lineage_data_store

    with get_lineage_data_store().database.transaction() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
        if not _dispatch_table_exists(connection, "family_task_dispatches"):
            return
        connection.execute("LOCK TABLE family_task_dispatches IN SHARE ROW EXCLUSIVE MODE")
        lineages, retain_table = _legacy_dispatch_scope(connection)
        readers = family_task_dispatches.live_dispatch_readers(get_repository()) if lineages else None
        for lineage_id in lineages:
            candidates, blockers, dispositions = _dispatch_candidates(connection, lineage_id, readers)
            if blockers:
                message_id, reason = blockers[0]
                raise ValueError(f"Cannot retire Family task {message_id}: {reason}")
            _write_cells(connection, lineage_id, candidates)
            _archive_dispatch_dispositions(connection, lineage_id, dispositions)
            if retain_table:
                # Consume only processed rows so a retry never reuses old receipts.
                connection.execute("DELETE FROM family_task_dispatches WHERE lineage_id=%s", (lineage_id,))
        if not retain_table:
            connection.execute("DROP TABLE family_task_dispatches")


def _dispatch_candidates(connection, lineage_id, readers):
    families = {row["resident_id"]: row["family_id"] for row in connection.execute("""
        SELECT r.resident_id, f.family_id FROM family_residents r JOIN families f USING(family_id)
        WHERE f.lineage_id=%s AND f.deleted_at IS NULL AND r.role='facade'
    """, (lineage_id,)).fetchall()}
    candidates = {}
    blockers = []
    dispositions = {}
    for receipt in family_task_dispatches.plan_legacy_dispatches(connection, lineage_id, *readers):
        if receipt["state"] == "blocked":
            message_id = receipt["message_id"]
            entry, reason = _plan_abandonment(connection, lineage_id, message_id, receipt["reason"])
            if entry is None:
                blockers.append((message_id, reason))
            else:
                dispositions[message_id] = entry
            continue
        root = receipt.get("source_message_id")
        if receipt["state"] != "accepted" or not root:
            continue
        facade = receipt["facade_id"]
        if _input_status(connection, facade, root) in {"completed", "cancelled"}:
            continue
        if facade not in families:
            blockers.append((receipt["message_id"], "missing_live_facade"))
            continue
        key = f"family.{families[facade]}.dispatch_receipts"
        candidates.setdefault(key, {})[receipt["message_id"]] = receipt
    return candidates, blockers, dispositions

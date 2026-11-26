"""Move active Family policy facts into Lineage Shared State at cutover."""
from __future__ import annotations

from copy import deepcopy
from typing import Callable

from management_common.json_columns import json_text, json_value

from . import family_review, family_task_dispatches, lineage_graph_state, lineage_primitives
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


def migrate_family_state(connection, lineage_id):
    """Copy verified policy cells in the caller's stopped-owner transaction.

    The outer cutover owns removing source fields and the intent table when
    their last consumer crosses over. This function changes only Shared State.
    """
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
    return _write_cells(connection, lineage_id, candidates)


def _write_cells(connection, lineage_id, candidates):
    if not candidates:
        return []
    stored = connection.execute(
        "SELECT cells_json FROM lineage_shared_state WHERE lineage_id=%s FOR UPDATE", (lineage_id,),
    ).fetchone()
    state = lineage_graph_state._normalize(json_value(stored["cells_json"]) if stored else {})
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
        connection.execute(
            "INSERT INTO lineage_shared_state (lineage_id,cells_json) VALUES (%s,%s) "
            "ON CONFLICT(lineage_id) DO UPDATE SET cells_json=excluded.cells_json",
            (lineage_id, json_text(state, ensure_ascii=False, sort_keys=True)),
        )
    return changed


def _same_cell(left, right):
    return json_text(left, sort_keys=True) == json_text(right, sort_keys=True)


def _legacy_dispatch_lineages(connection):
    return [row["lineage_id"] for row in connection.execute("""
        SELECT DISTINCT t.lineage_id FROM family_task_dispatches t JOIN lineages l USING(lineage_id)
        WHERE l.deleted_at IS NULL ORDER BY t.lineage_id
    """).fetchall()]


def _has_legacy_dispatch_table(connection):
    row = connection.execute(
        "SELECT to_regclass(quote_ident(current_schema()) || '.family_task_dispatches') AS name"
    ).fetchone()
    return bool(row and row["name"])


def check_legacy_dispatches(connection):
    """Check live legacy intents on the caller's snapshot without initializing stores."""
    if not _has_legacy_dispatch_table(connection):
        return []
    readers = family_task_dispatches.snapshot_dispatch_readers(connection)
    issues = []
    for lineage_id in _legacy_dispatch_lineages(connection):
        candidates, blockers = _dispatch_candidates(connection, lineage_id, readers)
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
        if not _has_legacy_dispatch_table(connection):
            return
        connection.execute("LOCK TABLE family_task_dispatches IN SHARE ROW EXCLUSIVE MODE")
        for lineage_id in _legacy_dispatch_lineages(connection):
            readers = family_task_dispatches.live_dispatch_readers(get_repository())
            candidates, blockers = _dispatch_candidates(connection, lineage_id, readers)
            if blockers:
                message_id, reason = blockers[0]
                raise ValueError(f"Cannot retire Family task {message_id}: {reason}")
            _write_cells(connection, lineage_id, candidates)
        connection.execute("DROP TABLE family_task_dispatches")


def _dispatch_candidates(connection, lineage_id, readers):
    families = {row["resident_id"]: row["family_id"] for row in connection.execute("""
        SELECT r.resident_id, f.family_id FROM family_residents r JOIN families f USING(family_id)
        WHERE f.lineage_id=%s AND f.deleted_at IS NULL AND r.role='facade'
    """, (lineage_id,)).fetchall()}
    candidates = {}
    blockers = []
    for receipt in family_task_dispatches.plan_legacy_dispatches(connection, lineage_id, *readers):
        if receipt["state"] == "blocked":
            blockers.append((receipt["message_id"], receipt["reason"]))
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
    return candidates, blockers

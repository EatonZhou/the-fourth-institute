"""Bounded graph activations and references to independent Agent executions.

Graph work is stored once, in typed path/step relations. Resident status and
output are read from Agent storage; accepting a Resident input ends propagation.
Finished paths are kept for RETENTION_DAYS, except each Effect's latest one.
"""
from __future__ import annotations

import json
import time
from management_common.database_time import decode_times, iso_time
from management_common.json_columns import json_value
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from management_common.portal_database import _database_schema
from . import trigger_path_storage as storage
from .domain_ports import failure_notification_port
from management_portal.execution import input_continuations

TERMINAL = storage.TERMINAL
RETENTION_DAYS = 30
PRUNE_BATCH_SIZE = 200


def data_store():
    from management_portal.execution.agent_data_store import get_agent_data_store
    from .state_store import get_lineage_data_store
    get_agent_data_store()
    return get_lineage_data_store()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def execution_cause(resident_id: str, message_id: str) -> dict[str, str]:
    from management_portal.execution import execution_inputs
    from management_portal.execution.agent_data_store import get_agent_data_store
    execution = execution_inputs.latest(get_agent_data_store(), resident_id, message_id)
    if not execution or execution["status"] not in TERMINAL:
        raise ValueError("Resident completion requires its terminal Agent Execution")
    return {"kind": "execution", "id": execution["execution_id"]}


def begin_resident_output(lineage_id: str, resident_id: str, message_id: str, *, at: str, actor: str = ""):
    """An emitted packet begins fresh graph propagation after a Resident leaf."""
    from . import lineage_primitives
    cause = execution_cause(resident_id, message_id)
    from management_common.principal import OperationIdentity
    from management_portal.execution.agent_data_store import get_agent_data_store
    from .resident_conversation_stream import get_repository
    execution = get_agent_data_store().execution(cause["id"])
    business_ref = get_repository().message_business_ref(resident_id, message_id)
    event = lineage_primitives.make_event(
        "resident.work_finished", subject_resident_id=resident_id, occurred_at=execution["finished_at"],
        identity=OperationIdentity.for_actor(actor or execution["actor_ref"]).to_dict(),
        payload={"message_id": message_id, "execution_id": cause["id"], "cause": cause,
                 **({"input_business_ref": business_ref} if business_ref else {})},
        correlation="packet:" + cause["id"],
    )
    identity = begin_event(lineage_id, resident_id, event.to_dict(), cause_kind="execution", cause_id=cause["id"])
    return lineage_primitives.normalize_event({**event.to_dict(), "payload": {**event.payload, "trigger_path_id": identity}})


def accepted_input(lineage_id: str, event_id: str) -> dict[str, Any]:
    """The Event input first accepted for this occurrence, or an empty mapping."""
    with data_store().database.transaction() as connection:
        row = connection.execute("SELECT input_json FROM trigger_paths WHERE trigger_path_id=%s",
                                 (storage.path_id(lineage_id, "event", event_id),)).fetchone()
    return json_value(row["input_json"]) if row else {}


def agent_executions_table() -> str:
    return '"' + _database_schema("agent") + '".executions'


def begin_event(lineage_id: str, source_id: str, event: Mapping[str, Any], *, cause_kind: str, cause_id: str) -> str:
    event = deepcopy(dict(event))
    event.setdefault("payload", {})
    event["payload"].pop("trigger_path_id", None)
    event["payload"].pop("trigger_step_id", None)
    at = str(event["occurred_at"])
    with data_store().database.transaction() as connection:
        # Delivery retries reconstruct the same occurrence. Its first accepted
        # timestamp, including its wire representation, is authoritative. SQL
        # TIMESTAMPTZ formatting may change +00:00 to Z or expand milliseconds;
        # using it here would turn identical input into a conflicting delivery.
        existing = connection.execute("SELECT input_json FROM trigger_paths WHERE trigger_path_id=%s",
                                      (storage.path_id(lineage_id, "event", event["id"]),)).fetchone()
        if existing:
            at = event["occurred_at"] = json_value(existing["input_json"])["occurred_at"]
        identity = storage.begin(connection, lineage_id=lineage_id, root_kind="event", occurrence_id=event["id"],
                                 root_node_id=source_id, cause_kind=cause_kind, cause_id=cause_id, occurred_at=at, input=event)
        storage.admit_step(connection, trigger_path_id=identity, node_id=source_id, kind="event",
                           activation_key=event["id"], created_at=at)
        return identity


def event_settled(identity: str, source_id: str, occurrence_id: str, *, error: str = "") -> None:
    with data_store().database.transaction() as connection:
        # A persisted delivery still owns transport recovery. Accepting its
        # Event is final, while the delivery step remains waiting.
        waiting = connection.execute(
            "SELECT 1 FROM trigger_path_steps WHERE trigger_path_id=%s "
            "AND kind<>'event' AND status IN ('pending','waiting','running') LIMIT 1", (identity,),
        ).fetchone()
        step = storage.step_id(identity, "event", source_id, occurrence_id)
        settled = connection.execute("SELECT status FROM trigger_path_steps WHERE step_id=%s", (step,)).fetchone()
        if settled and settled["status"] in storage.STEP_TERMINAL:
            _settle(connection, identity)
            return
        storage.transition_step(connection, step,
                                status="failed" if error and not waiting else "completed", at=now(),
                                result={"error": error} if error else {})
        _settle(connection, identity)


def _settle(connection: Any, identity: str) -> str:
    path = storage._lock(connection, identity)
    if path["status"] != "active":
        return path["status"]
    executions = connection.execute(
        f"SELECT e.execution_id,e.status,e.finished_at FROM {agent_executions_table()} e "
        "JOIN trigger_path_execution_links l USING(execution_id) JOIN trigger_path_steps s USING(step_id) "
        "WHERE s.trigger_path_id=%s", (identity,),
    ).fetchall()
    missing = connection.execute(
        "SELECT DISTINCT s.step_id,s.node_id,s.input_message_id FROM trigger_path_steps s "
        "JOIN trigger_path_execution_links l USING(step_id) "
        f"LEFT JOIN {agent_executions_table()} e USING(execution_id) "
        "WHERE s.trigger_path_id=%s AND e.execution_id IS NULL", (identity,),
    ).fetchall()
    outcomes = input_continuations.outcomes(connection, executions)
    status = storage.finish(connection, identity, at=now(),
                            execution_statuses={key: row["status"] for key, row in outcomes.items()},
                            execution_finished_at={key: row["finished_at"] for key, row in outcomes.items()},
                            input_states=input_receipts(missing))
    if status == "failed":
        from .invariant_locks import defer_until_state_unlock
        data_store().database.after_commit(lambda: defer_until_state_unlock(lambda: failure_notification_port.notify_failed_path(identity)))
    return status


def input_receipts(steps: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Read durable input receipts without requiring retained execution history."""
    from .resident_conversation_stream import get_repository
    steps = list(steps)
    states = get_repository().input_states_by_id([(s["node_id"], s["input_message_id"]) for s in steps]) if steps else {}
    return {s["step_id"]: states.get((s["node_id"], s["input_message_id"]), {}) for s in steps}


def settle(identity: str) -> bool:
    with data_store().database.transaction() as connection:
        row = storage._lock(connection, identity)
        return row["status"] == "active" and _settle(connection, identity) in TERMINAL


def prune_finished(*, now: datetime | None = None, time_budget_seconds: float = 2.0) -> int:
    """Delete paths finished more than RETENTION_DAYS ago, in small transactions.

    Housekeeping calls this repeatedly; a backlog drains over several rounds.
    """
    before = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    deadline = time.monotonic() + time_budget_seconds
    database = data_store().database
    with database.read() as connection:
        lineage_ids = storage.finished_lineage_ids(connection)
    removed = 0
    for lineage_id in lineage_ids:
        while time.monotonic() < deadline:
            with database.transaction() as connection:
                count = storage.prune_finished(connection, lineage_id=lineage_id, before=before, limit=PRUNE_BATCH_SIZE)
            removed += count
            if count < PRUNE_BATCH_SIZE:
                break
        if time.monotonic() >= deadline:
            break
    return removed


def event_input(lineage_id: str, event_id: str) -> dict[str, Any] | None:
    """The original Event behind an operational failure, within its Lineage."""
    occurrence = event_occurrence(lineage_id, event_id)
    return occurrence["input"] if occurrence else None


def event_occurrence(lineage_id: str, event_id: str) -> dict[str, Any] | None:
    """The accepted fact and its bounded propagation status, without reobserving it."""
    with data_store().database.read() as connection:
        row = connection.execute("SELECT trigger_path_id,root_node_id,status,input_json FROM trigger_paths WHERE lineage_id=%s "
                                 "AND root_kind='event' AND occurrence_id=%s LIMIT 1", (lineage_id, event_id)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["input"] = json_value(result.pop("input_json"))
    return result


def get(lineage_id: str, identity: str) -> dict[str, Any] | None:
    with data_store().database.transaction() as connection:
        row = connection.execute("SELECT lineage_id FROM trigger_paths WHERE trigger_path_id=%s", (identity,)).fetchone()
        if row is None or row["lineage_id"] != lineage_id:
            return None
        result = storage.read(connection, identity)
        result["executions"] = [decode_times(row, {"created_at", "started_at", "finished_at"}) for row in connection.execute(
            f"SELECT DISTINCT e.execution_id,e.agent_id,e.status,e.created_at,e.started_at,e.finished_at,e.final_message_id "
            f"FROM {agent_executions_table()} e JOIN trigger_path_execution_links l USING(execution_id) "
            "JOIN trigger_path_steps s USING(step_id) WHERE s.trigger_path_id=%s", (identity,),
        ).fetchall()]
        return result



def find_event_paths(lineage_id: str, payload: Mapping[str, str], *, limit: int = 10) -> list[dict[str, Any]]:
    """Recent propagation attempts for an exact event payload, including failed effects."""
    if not payload:
        raise ValueError("An event payload selector is required")
    with data_store().database.read() as connection:
        rows = connection.execute(
            "SELECT trigger_path_id,status,occurred_at,finished_at FROM trigger_paths "
            "WHERE lineage_id=%s AND input_json::jsonb->'payload' @> %s::jsonb "
            "ORDER BY occurred_at DESC,trigger_path_id DESC LIMIT %s",
            (lineage_id, json.dumps(dict(payload)), max(1, min(limit, 50))),
        ).fetchall()
        results = [decode_times(row, {"occurred_at", "finished_at"}) for row in rows]
        for item in results:
            steps = connection.execute(
                "SELECT step_id,node_id,status,result_json FROM trigger_path_steps "
                "WHERE trigger_path_id=%s ORDER BY finished_at DESC", (item["trigger_path_id"],),
            ).fetchall()
            links = connection.execute(
                f"SELECT l.step_id,e.execution_id,e.status,e.finished_at FROM trigger_path_execution_links l "
                f"JOIN {agent_executions_table()} e USING(execution_id) JOIN trigger_path_steps s USING(step_id) "
                "WHERE s.trigger_path_id=%s", (item["trigger_path_id"],),
            ).fetchall()
            outcomes = input_continuations.outcomes(connection, links)
            failed = {r["step_id"] for r in links if outcomes[r["execution_id"]]["status"] in {"failed", "cancelled"}}
            item["failures"] = [{"node_id": r["node_id"], "result": json_value(r["result_json"] or "{}")}
                                for r in steps if r["status"] in {"failed", "cancelled"} or r["step_id"] in failed]
    return results


def list_paths(lineage_id: str, *, limit: int = 50, offset: int = 0, resident_id: str = "") -> tuple[list[dict[str, Any]], int]:
    where, params = "p.lineage_id=%s", [lineage_id]
    if resident_id:
        where += " AND EXISTS (SELECT 1 FROM trigger_path_steps s WHERE s.trigger_path_id=p.trigger_path_id AND s.kind='resident' AND s.node_id=%s)"
        params.append(resident_id)
    with data_store().database.read() as connection:
        total = connection.execute("SELECT count(*) FROM trigger_paths p WHERE " + where, tuple(params)).fetchone()[0]
        rows = connection.execute("SELECT p.* FROM trigger_paths p WHERE " + where +
                                  " ORDER BY p.occurred_at DESC,p.trigger_path_id LIMIT %s OFFSET %s",
                                  (*params, limit, offset)).fetchall()
    return [{**{key: value for key, value in decode_times(row, {"occurred_at", "finished_at"}).items() if key != "input_json"},
             "input": json_value(row["input_json"])} for row in rows], total


def admit_resident(lineage_id: str, *, resident_id: str, message_id: str, trigger_path_id: str,
                   source_wire_id: str, event: Mapping[str, Any], queued_at: str = "") -> dict[str, Any]:
    with data_store().database.transaction() as connection:
        path = storage._lock(connection, trigger_path_id)
        if path["lineage_id"] != lineage_id:
            raise ValueError("Resident handoff belongs to another Lineage")
        parent = str((event.get("payload") or {}).get("trigger_step_id") or "")
        if not parent:
            parent = storage.step_id(trigger_path_id, "event", path["root_node_id"], path["occurrence_id"])
        storage.admit_step(connection, trigger_path_id=trigger_path_id, kind="resident", node_id=resident_id,
                           activation_key=message_id, input_message_id=message_id, created_at=queued_at or now(),
                           checkpoint={"event": dict(event), "source_wire_id": source_wire_id},
                           predecessors=[(parent, source_wire_id)])
    from management_portal.execution import execution_inputs
    from management_portal.execution.agent_data_store import get_agent_data_store
    execution = execution_inputs.latest(get_agent_data_store(), resident_id, message_id)
    if execution:
        link_input(lineage_id, resident_id, message_id, execution["execution_id"])
    return resident_input(lineage_id, resident_id, message_id)


def link_input(lineage_id: str, resident_id: str, message_id: str, execution_id: str) -> bool:
    """Associate every source step of a merged input with its one Execution."""
    with data_store().database.transaction() as connection:
        rows = connection.execute("SELECT s.step_id,s.trigger_path_id FROM trigger_path_steps s "
                                  "JOIN trigger_paths p USING(trigger_path_id) "
                                  "WHERE p.lineage_id=%s AND s.node_id=%s AND s.input_message_id=%s",
                                  (lineage_id, resident_id, message_id)).fetchall()
        execution = connection.execute(f"SELECT agent_id,COALESCE(started_at,created_at) AS accepted_at FROM {agent_executions_table()} WHERE execution_id=%s", (execution_id,)).fetchone()
        if rows and (execution is None or execution["agent_id"] != resident_id):
            raise ValueError("Resident handoff must reference its own durable Execution")
        for row in rows:
            from management_portal.execution import agent_data_input_retries as input_retries
            predecessors = [link["execution_id"] for link in connection.execute(
                "SELECT execution_id FROM trigger_path_execution_links WHERE step_id=%s", (row["step_id"],),
            ).fetchall()]
            if input_retries.independent_of(connection, predecessors=predecessors, execution_id=execution_id,
                                            resident_id=resident_id, message_id=message_id):
                # The owner authorized new work on the same input, not another
                # delivery by this historical path. Keep its facts untouched.
                continue
            storage.link_execution(connection, row["step_id"], execution_id)
            storage.transition_step(connection, row["step_id"], status="completed", at=iso_time(execution["accepted_at"]))
            _settle(connection, row["trigger_path_id"])
        return bool(rows)


def resident_inputs(lineage_id: str, *, paths: Iterable[str] = (), resident_id: str = "", message_id: str = "",
                    pending: bool = False, finished_since: datetime | None = None) -> list[dict[str, Any]]:
    """Read the Resident leaves, joining Execution facts instead of copying them.

    ``finished_since`` leaves out Runs that finished before it.
    """
    clauses = ["p.lineage_id=%s", "s.kind='resident'"]; params: list[Any] = [lineage_id]
    if pending:
        clauses.append("NOT s.checkpoint_json::jsonb ? 'recovery_blocked'")
        # Only a handoff still open, or accepted by an Execution not over yet,
        # can be pending; the status rule below decides among these few.
        clauses.append("s.step_id IN (SELECT step_id FROM trigger_path_steps WHERE status IN ('pending','running','waiting') "
                       "AND NOT checkpoint_json::jsonb ? 'recovery_blocked' UNION SELECT l.step_id "
                       f"FROM {agent_executions_table()} e JOIN trigger_path_execution_links l USING(execution_id) "
                       "WHERE e.status IN ('preparing','queued','running') UNION SELECT l.step_id "
                       "FROM trigger_path_execution_links l JOIN trigger_path_steps leaf USING(step_id) "
                       f"JOIN \"{_database_schema('agent')}\".agent_operations o "
                       "ON o.operation_id='execution_handoff:' || l.execution_id "
                       "JOIN trigger_paths run USING(trigger_path_id) WHERE run.lineage_id=%s AND run.status='active')")
        params.append(lineage_id)
    if finished_since is not None:
        # A finished Run's handoffs finished by its finished_at, unless the Run
        # was cancelled while an Execution it had accepted went on.
        clauses.append("s.trigger_path_id IN (SELECT trigger_path_id FROM trigger_paths WHERE lineage_id=%s "
                       "AND status='active' UNION SELECT trigger_path_id FROM trigger_paths WHERE lineage_id=%s "
                       "AND status<>'active' AND finished_at>=%s UNION SELECT run.trigger_path_id FROM trigger_paths run "
                       "WHERE run.lineage_id=%s AND run.status='cancelled' AND EXISTS (SELECT 1 FROM trigger_path_steps linked "
                       "JOIN trigger_path_execution_links l USING(step_id) "
                       f"JOIN {agent_executions_table()} e USING(execution_id) "
                       "WHERE linked.trigger_path_id=run.trigger_path_id AND e.finished_at>=%s))")
        params.extend((lineage_id, lineage_id, finished_since, lineage_id, finished_since))
    wanted = tuple(paths)
    if wanted:
        clauses.append("s.trigger_path_id=ANY(%s)"); params.append(list(wanted))
    for column, value in (("node_id", resident_id), ("input_message_id", message_id)):
        if value:
            clauses.append("s." + column + "=%s"); params.append(value)
    with data_store().database.read_current() as connection:
        # Lineages differ in size a hundredfold. A cached generic plan assumes
        # an average one and walks a large Lineage's paths for the few candidates.
        rows = connection.execute("SELECT s.*,p.input_json FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
                                  "WHERE " + " AND ".join(clauses) + " ORDER BY s.created_at,s.step_id", tuple(params),
                                  prepare=False if pending or finished_since is not None else None).fetchall()
        if not rows:
            return []
        edges = connection.execute("SELECT target_step_id,wire_id FROM trigger_path_step_edges WHERE target_step_id=ANY(%s)",
                                   ([r["step_id"] for r in rows],)).fetchall()
        links = connection.execute(f"SELECT l.step_id,e.execution_id,e.status,e.started_at,e.finished_at,e.final_message_id "
                                   f"FROM trigger_path_execution_links l JOIN {agent_executions_table()} e USING(execution_id) "
                                   "WHERE l.step_id=ANY(%s)", ([r["step_id"] for r in rows],)).fetchall()
        outcomes = input_continuations.outcomes(connection, links)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        grouped.setdefault(link["step_id"], []).append(dict(link))
    wires: dict[str, str] = {}
    for edge in edges:
        wires.setdefault(edge["target_step_id"], edge["wire_id"])
    results = []
    for row in rows:
        executions = grouped.get(row["step_id"], [])
        states = [outcomes[e["execution_id"]]["status"] for e in executions]
        status = row["status"]
        if executions:
            status = "failed" if "failed" in states else "cancelled" if "cancelled" in states else (
                "completed" if all(s == "completed" for s in states) else "running" if "running" in states else "queued")
        elif status in {"pending", "waiting"}:
            status = "queued"
        if pending and status in TERMINAL:
            continue
        checkpoint = json_value(row["checkpoint_json"])
        item = {"step_id": row["step_id"], "trigger_path_id": row["trigger_path_id"], "resident_id": row["node_id"],
                "message_id": row["input_message_id"], "event": checkpoint.get("event") or json_value(row["input_json"]),
                "source_wire_id": wires.get(row["step_id"], ""), "status": status, "queued_at": iso_time(row["created_at"]),
                "completed_at": iso_time(max((outcomes[e["execution_id"]]["finished_at"] for e in executions
                                               if outcomes[e["execution_id"]]["finished_at"] is not None),
                                              default=row["finished_at"])) or "",
                "executions": [decode_times(e, {"started_at", "finished_at"}) for e in executions]}
        if status not in TERMINAL:
            item["completed_at"] = ""
        if len(executions) == 1:
            item["execution_id"] = executions[0]["execution_id"]
        if checkpoint.get("recovery_blocked"):
            item["recovery_blocked"] = checkpoint["recovery_blocked"]
        results.append(item)
    return results


def block_unrecoverable_inputs(lineage_id: str, inputs: list[dict[str, Any]]) -> int:
    from .lineage_recovery_policy import permanent_error
    count = 0
    for item in inputs:
        error = permanent_error(item.get("event") or {})
        if not error:
            continue
        with data_store().database.transaction() as c:
            path = storage._lock(c, item["trigger_path_id"])
            if path["lineage_id"] != lineage_id or path["status"] != "active":
                continue
            row = c.execute("SELECT checkpoint_json,input_message_id,status FROM trigger_path_steps WHERE step_id=%s AND trigger_path_id=%s", (item["step_id"], item["trigger_path_id"])).fetchone()
            if not row or row["status"] in TERMINAL or row["input_message_id"] != item["message_id"]:
                continue
            checkpoint = json_value(row["checkpoint_json"])
            current_event = checkpoint.get("event") or json_value(path["input_json"])
            if current_event != item["event"] or checkpoint.get("recovery_blocked"):
                continue
            checkpoint["recovery_blocked"] = {"error": error, "at": now(), "requires_reissue": True}
            c.execute("UPDATE trigger_path_steps SET checkpoint_json=%s WHERE step_id=%s", (storage.encode(checkpoint), item["step_id"]))
            storage._touch(c, item["trigger_path_id"])
            count += 1
    return count


def resident_input(lineage_id: str, resident_id: str, message_id: str) -> dict[str, Any] | None:
    rows = resident_inputs(lineage_id, resident_id=resident_id, message_id=message_id)
    return rows[0] if rows else None


def settle_input(lineage_id: str, resident_id: str, message_id: str, *, status: str, at: str,
                 execution_id: str = "") -> list[dict[str, Any]]:
    if not execution_id:
        from management_portal.execution import execution_inputs
        from management_portal.execution.agent_data_store import get_agent_data_store
        execution = execution_inputs.latest(get_agent_data_store(), resident_id, message_id)
        execution_id = str((execution or {}).get("execution_id") or "")
    if execution_id:
        link_input(lineage_id, resident_id, message_id, execution_id)
    rows = resident_inputs(lineage_id, resident_id=resident_id, message_id=message_id)
    from .resident_conversation_stream import get_repository
    try:
        receipt = get_repository().input_state(resident_id, message_id)
    except KeyError:
        receipt = {}
    with data_store().database.transaction() as connection:
        for row in rows:
            if not row["executions"]:
                if status == "completed" and receipt.get("state") != "completed":
                    raise ValueError("Successful Resident work requires a terminal input receipt or Execution")
                storage.transition_step(connection, row["step_id"], status=status,
                                        at=receipt.get("updated_at") if receipt.get("state") == status else at,
                                        input_status=str(receipt.get("state") or ""))
            _settle(connection, row["trigger_path_id"])
    return resident_inputs(lineage_id, resident_id=resident_id, message_id=message_id)


def effect_started(lineage_id: str, activation_key: str, record: Mapping[str, Any]) -> tuple[str, str]:
    with data_store().database.transaction() as connection:
        identity = str(record.get("trigger_path_id") or "")
        at = str(record["started_at"])
        if not identity:
            identity = storage.begin(connection, lineage_id=lineage_id, root_kind="effect", occurrence_id=activation_key,
                                     root_node_id=record["effect_id"], cause_kind="instruction" if record.get("instruction_id") else "external",
                                     cause_id=str(record.get("instruction_id") or activation_key), occurred_at=at, input={})
        path = storage._lock(connection, identity)
        predecessors = []
        parent = str(record.get("trigger_step_id") or "")
        if not parent and path["root_kind"] == "event":
            parent = storage.step_id(identity, "event", path["root_node_id"], path["occurrence_id"])
        if parent:
            predecessors.append((parent, str(record.get("source_wire_id") or "")))
        step = storage.admit_step(connection, trigger_path_id=identity, kind="effect", node_id=record["effect_id"],
                                  activation_key=activation_key, created_at=at, predecessors=predecessors)
        storage.transition_step(connection, step, status="running", at=at)
        return identity, step


def operator_started(lineage_id: str, node_id: str, event: Mapping[str, Any]) -> str:
    identity = event["payload"]["trigger_path_id"]
    with data_store().database.transaction() as connection:
        path = storage._lock(connection, identity)
        if path["lineage_id"] != lineage_id:
            raise ValueError("Operator belongs to another Lineage")
        parent = event["payload"].get("trigger_step_id") or storage.step_id(identity, "event", path["root_node_id"], path["occurrence_id"])
        step = storage.admit_step(connection, trigger_path_id=identity, kind="operator", node_id=node_id,
                                  activation_key=event["id"], created_at=event["occurred_at"], predecessors=[(parent, "")])
        return step


def operator_settled(step: str, *, status: str, result: Mapping[str, Any] | None = None) -> None:
    with data_store().database.transaction() as connection:
        storage.transition_step(connection, step, status=status, at=now(), result=result)


def effect_finished(lineage_id: str, activation_key: str) -> dict[str, Any] | None:
    with data_store().database.read_current() as connection:
        row = connection.execute("SELECT s.* FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
                                 "WHERE p.lineage_id=%s AND s.kind='effect' AND s.activation_key=%s "
                                 "AND s.status IN ('completed','failed','cancelled')", (lineage_id, activation_key)).fetchone()
    if row is None:
        return None
    result = json_value(row["result_json"])
    return {**result, "effect_id": row["node_id"], "trigger_path_id": row["trigger_path_id"],
            "trigger_step_id": row["step_id"],
            "status": result.get("termination_reason") or ("succeeded" if row["status"] == "completed" else row["status"]),
            "started_at": iso_time(row["started_at"]), "finished_at": iso_time(row["finished_at"])}


def effect_settled(lineage_id: str, activation_key: str, record: Mapping[str, Any]) -> bool:
    with data_store().database.transaction() as connection:
        row = connection.execute("SELECT s.step_id,s.trigger_path_id FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
                                 "WHERE p.lineage_id=%s AND s.kind='effect' AND s.activation_key=%s", (lineage_id, activation_key)).fetchone()
        if row is None:
            raise ValueError("Effect completion requires its admitted activation")
        result = {key: deepcopy(record[key]) for key in ("outputs", "failure", "disposition", "calls") if key in record}
        if record["status"] == "timed_out":
            result["termination_reason"] = "timed_out"
        storage.transition_step(connection, row["step_id"], status={"succeeded": "completed", "timed_out": "failed"}.get(record["status"], record["status"]),
                                at=record["finished_at"], result=result)
        _settle(connection, row["trigger_path_id"])
        return True


def settle_disposed_delivery(lineage_id: str, activation_key: str) -> None:
    """Finish only the queued graph delivery of a manually disposed Effect.

    Edges retain the occurrence's wire and predecessor after its queue/journal
    have gone, so the same operation also repairs an older disposition. A wire
    name alone is insufficient: other events and branches must stay untouched.
    """
    from management_portal.hatg.contracts import InstructionError

    with data_store().database.transaction() as connection:
        effect = connection.execute(
            "SELECT s.* FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
            "WHERE p.lineage_id=%s AND s.kind='effect' AND s.activation_key=%s",
            (lineage_id, activation_key),
        ).fetchone()
        if effect is None:
            raise ValueError("Effect disposition requires its admitted activation")
        identity = effect["trigger_path_id"]
        path = storage._lock(connection, identity)
        # Queued deliveries are rooted at the original Event even when an
        # intermediate operator becomes the Effect's immediate predecessor.
        event_root = (storage.step_id(identity, "event", path["root_node_id"], path["occurrence_id"])
                      if path["root_kind"] == "event" else "")
        disposition = json_value(effect["result_json"]).get("disposition") or {}
        status = {"succeeded": "completed", "failed": "failed"}.get(disposition.get("status"))
        if not status or effect["status"] != status:
            raise InstructionError("instruction.conflict", "Effect 尚无人工处置终态")
        deliveries = connection.execute(
            "SELECT DISTINCT d.* FROM trigger_path_steps d "
            "JOIN trigger_path_step_edges e ON e.trigger_path_id=d.trigger_path_id "
            "AND e.target_step_id=%s AND e.wire_id=d.node_id "
            "WHERE d.trigger_path_id=%s AND d.kind='operator' AND e.wire_id<>'' "
            "AND (e.source_step_id=d.step_id OR EXISTS ("
            "SELECT 1 FROM trigger_path_step_edges p WHERE p.trigger_path_id=d.trigger_path_id "
            "AND p.target_step_id=d.step_id AND p.wire_id=e.wire_id "
            "AND (p.source_step_id=e.source_step_id OR p.source_step_id=%s)))",
            (effect["step_id"], identity, event_root),
        ).fetchall()
        if len(deliveries) > 1:
            raise InstructionError("instruction.conflict", "Effect 对应多个连线投递步骤，无法确认本次处置目标")
        if deliveries and deliveries[0]["status"] not in storage.STEP_TERMINAL:
            delivery = deliveries[0]
            if delivery["status"] != "waiting":
                raise InstructionError("instruction.conflict", "Effect 对应连线投递步骤仍在执行")
            storage.transition_step(
                connection, delivery["step_id"], status=status, at=now(),
                result={**json_value(delivery["result_json"]), "disposition": disposition},
            )
        _settle(connection, identity)


def dispose_open_steps(lineage_id: str, identity: str, disposition: Mapping[str, Any]) -> str:
    """Fail the path's remaining open steps under an admin disposition, then settle it."""
    with data_store().database.transaction() as connection:
        path = storage._lock(connection, identity)
        if path["lineage_id"] != lineage_id:
            raise ValueError("Trigger Path belongs to another Lineage")
        if path["status"] != "active":
            return path["status"]
        for step in connection.execute("SELECT step_id,result_json FROM trigger_path_steps WHERE trigger_path_id=%s "
                                       "AND status IN ('pending','running','waiting')", (identity,)).fetchall():
            storage.transition_step(connection, step["step_id"], status="failed", at=disposition["occurred_at"],
                                    result={**json_value(step["result_json"]), "disposition": dict(disposition)})
        return _settle(connection, identity)


def delivery_waiting(lineage_id: str, wire_id: str, delivery: Mapping[str, Any]) -> None:
    event = delivery["event"]
    identity = (event.get("payload") or {})["trigger_path_id"]
    with data_store().database.transaction() as connection:
        path = storage._lock(connection, identity)
        if path["lineage_id"] != lineage_id:
            raise ValueError("Delivery belongs to another Lineage")
        parent = storage.step_id(identity, "event", path["root_node_id"], path["occurrence_id"])
        step = storage.admit_step(connection, trigger_path_id=identity, node_id=wire_id, kind="operator",
                                  activation_key=str(delivery["cursor"]), created_at=delivery["queued_at"],
                                  predecessors=[(parent, wire_id)])
        storage.transition_step(connection, step, status="waiting", at=delivery["queued_at"])


def delivery_settled(lineage_id: str, wire_id: str, delivery: Mapping[str, Any], *, status: str = "completed", error: str = "") -> None:
    identity = (delivery["event"].get("payload") or {})["trigger_path_id"]
    with data_store().database.transaction() as connection:
        storage.transition_step(connection, storage.step_id(identity, "operator", wire_id, str(delivery["cursor"])),
                                status=status, at=now(), result={"error": error} if error else None)
        _settle(connection, identity)

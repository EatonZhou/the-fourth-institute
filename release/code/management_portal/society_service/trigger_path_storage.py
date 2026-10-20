"""Trigger occurrences and their bounded, durable graph evaluation.

A path owns graph progress and references Resident executions. Executions do
not own paths. New business events open new paths; internal port propagation
keeps the current path. All changes join the caller's database transaction.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from management_common.json_columns import json_text, json_value
from management_common.database_time import instant, decode_times


TERMINAL = frozenset({"completed", "failed", "cancelled"})
STEP_TERMINAL = TERMINAL | {"skipped"}
STEP_KINDS = frozenset({"event", "operator", "effect", "resident"})
CAUSE_KINDS = frozenset({"input", "timer", "execution", "instruction", "step", "external"})
COLUMNS = {
    "trigger_paths": (
        "trigger_path_id", "lineage_id", "root_kind", "occurrence_id", "root_node_id",
        "cause_kind", "cause_id", "occurred_at", "input_json", "status", "finished_at", "revision",
    ),
    "trigger_path_steps": (
        "step_id", "trigger_path_id", "node_id", "kind", "activation_key", "input_message_id",
        "status", "created_at", "started_at", "finished_at", "checkpoint_json", "result_json",
    ),
    "trigger_path_step_edges": ("trigger_path_id", "source_step_id", "target_step_id", "wire_id"),
    "trigger_path_execution_links": ("step_id", "execution_id"),
}


def encode(value: Any) -> str:
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def path_id(lineage_id: str, root_kind: str, occurrence_id: str) -> str:
    if root_kind not in {"event", "effect"} or not lineage_id or not occurrence_id:
        raise ValueError("A Trigger Path requires a Lineage and an Event occurrence or Effect activation")
    digest = hashlib.sha256(encode([lineage_id, root_kind, occurrence_id]).encode()).hexdigest()
    return "trigger_path_" + digest[:12]


def step_id(trigger_path_id: str, kind: str, node_id: str, activation_key: str) -> str:
    if kind not in STEP_KINDS or not all((trigger_path_id, node_id, activation_key)):
        raise ValueError("A Trigger Path step requires its path, node and activation identity")
    digest = hashlib.sha256(encode([trigger_path_id, kind, node_id, activation_key]).encode()).hexdigest()
    return "trigger_step_" + digest[:12]


def create_schema(connection: Any) -> None:
    connection.execute("""CREATE TABLE trigger_paths (
        trigger_path_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL,
        root_kind TEXT NOT NULL CHECK(root_kind IN ('event','effect')),
        occurrence_id TEXT NOT NULL, root_node_id TEXT NOT NULL,
        cause_kind TEXT NOT NULL CHECK(cause_kind IN ('input','timer','execution','instruction','step','external')),
        cause_id TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, input_json JSONB NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('active','completed','failed','cancelled')),
        finished_at TIMESTAMPTZ, revision BIGINT NOT NULL DEFAULT 1,
        UNIQUE(lineage_id,root_kind,occurrence_id),
        CHECK((status='active')=(finished_at IS NULL)))""")
    connection.execute("CREATE INDEX trigger_paths_lineage_idx ON trigger_paths(lineage_id,occurred_at DESC,trigger_path_id)")
    connection.execute("CREATE INDEX trigger_paths_active_idx ON trigger_paths(lineage_id) WHERE status='active'")
    connection.execute("""CREATE TABLE trigger_path_steps (
        step_id TEXT PRIMARY KEY, trigger_path_id TEXT NOT NULL REFERENCES trigger_paths(trigger_path_id),
        node_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('event','operator','effect','resident')),
        activation_key TEXT NOT NULL, input_message_id TEXT,
        status TEXT NOT NULL CHECK(status IN ('pending','running','waiting','completed','failed','cancelled','skipped')),
        created_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
        checkpoint_json JSONB NOT NULL DEFAULT '{}', result_json JSONB NOT NULL DEFAULT '{}',
        UNIQUE(trigger_path_id,kind,node_id,activation_key), UNIQUE(trigger_path_id,step_id),
        CHECK((status IN ('pending','running','waiting'))=(finished_at IS NULL)),
        CHECK(input_message_id IS NULL OR kind='resident'))""")
    connection.execute("CREATE INDEX trigger_path_steps_path_idx ON trigger_path_steps(trigger_path_id,created_at,step_id)")
    connection.execute("CREATE INDEX trigger_path_steps_input_idx ON trigger_path_steps(node_id,input_message_id) WHERE kind='resident'")
    connection.execute("""CREATE TABLE trigger_path_step_edges (
        trigger_path_id TEXT NOT NULL, source_step_id TEXT NOT NULL, target_step_id TEXT NOT NULL, wire_id TEXT NOT NULL,
        PRIMARY KEY(trigger_path_id,source_step_id,target_step_id,wire_id),
        FOREIGN KEY(trigger_path_id,source_step_id) REFERENCES trigger_path_steps(trigger_path_id,step_id),
        FOREIGN KEY(trigger_path_id,target_step_id) REFERENCES trigger_path_steps(trigger_path_id,step_id),
        CHECK(source_step_id<>target_step_id))""")
    connection.execute("""CREATE TABLE trigger_path_execution_links (
        step_id TEXT NOT NULL REFERENCES trigger_path_steps(step_id), execution_id TEXT NOT NULL,
        PRIMARY KEY(step_id,execution_id))""")
    connection.execute("CREATE INDEX trigger_path_execution_links_execution_idx ON trigger_path_execution_links(execution_id,step_id)")
    for name, definition in READ_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


# Runtime reads that need a few current rows out of the whole history.
READ_INDEXES = {
    # Each Effect's latest finished activation, which GetLineage shows.
    "trigger_path_steps_effect_latest_idx": "ON trigger_path_steps(node_id,finished_at DESC,step_id DESC) "
                                            "WHERE kind='effect' AND status IN ('completed','failed','cancelled')",
    # An Effect activation by its key, when the Effect starts and settles.
    "trigger_path_steps_effect_activation_idx": "ON trigger_path_steps(activation_key) WHERE kind='effect'",
    # Open steps recovery can still advance: pending Resident handoffs and the
    # paths recovery looks at. Blocked steps wait for an explicit reissue.
    "trigger_path_steps_unblocked_idx": "ON trigger_path_steps(trigger_path_id,step_id) "
                                        "WHERE status IN ('pending','running','waiting') "
                                        "AND NOT checkpoint_json::jsonb ? 'recovery_blocked'",
    # The wire that admitted a step.
    "trigger_path_step_edges_target_idx": "ON trigger_path_step_edges(target_step_id)",
    # Runs that finished within residents.all_finished's window, and cancelled
    # Runs whose accepted Executions may have finished later.
    "trigger_paths_finished_idx": "ON trigger_paths(lineage_id,finished_at) WHERE status<>'active'",
    "trigger_paths_cancelled_idx": "ON trigger_paths(lineage_id) WHERE status='cancelled'",
    # Propagation attempts for one Event payload, which Family recovery looks up.
    "trigger_paths_payload_idx": "ON trigger_paths USING gin ((input_json::jsonb->'payload') jsonb_path_ops)",
}


def _lock(connection: Any, identity: str) -> Any:
    row = connection.execute("SELECT * FROM trigger_paths WHERE trigger_path_id=%s FOR UPDATE", (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    return row


def _touch(connection: Any, identity: str) -> None:
    connection.execute("UPDATE trigger_paths SET revision=revision+1 WHERE trigger_path_id=%s", (identity,))


def begin(connection: Any, *, lineage_id: str, root_kind: str, occurrence_id: str,
          root_node_id: str, cause_kind: str, cause_id: str, occurred_at: str,
          input: Mapping[str, Any]) -> str:
    """Record the occurrence, including one that routes to no destinations.

    A duplicate occurrence must carry identical origin and input. Holding the
    row lock serializes graph admission with completion and cancellation.
    """
    identity = path_id(lineage_id, root_kind, occurrence_id)
    if cause_kind not in CAUSE_KINDS or not all((root_node_id, cause_id, occurred_at)):
        raise ValueError("A trigger occurrence requires its explicit origin and timestamp")
    values = (identity, lineage_id, root_kind, occurrence_id, root_node_id,
              cause_kind, cause_id, instant(occurred_at), encode(input))
    connection.execute(
        "INSERT INTO trigger_paths(trigger_path_id,lineage_id,root_kind,occurrence_id,root_node_id,"
        "cause_kind,cause_id,occurred_at,input_json,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'active') "
        "ON CONFLICT(trigger_path_id) DO NOTHING", values,
    )
    row = _lock(connection, identity)
    stored = (*(row[name] for name in COLUMNS["trigger_paths"][:8]), json_value(row["input_json"]))
    expected = (*values[:8], json_value(values[8]))
    if stored != expected:
        changed = [name for name, old, new in zip(COLUMNS["trigger_paths"][:9], stored, expected) if old != new]
        # Identify the owning occurrence without logging its potentially private payload.
        original = stored[8]
        input_fields = sorted(key for key in set(original) | set(input) if original.get(key) != input.get(key))
        raise ValueError("Conflicting delivery of an existing trigger occurrence: " + ", ".join(changed)
                         + f" (path={identity}, input_fields={','.join(input_fields)})")
    return identity


def admit_step(connection: Any, *, trigger_path_id: str, node_id: str, kind: str,
               activation_key: str, created_at: str, input_message_id: str | None = None,
               checkpoint: Mapping[str, Any] | None = None,
               predecessors: Iterable[tuple[str, str]] = ()) -> str:
    """Admit work before releasing its predecessor's transaction/lock."""
    identity = step_id(trigger_path_id, kind, node_id, activation_key)
    path = _lock(connection, trigger_path_id)
    existing = connection.execute("SELECT * FROM trigger_path_steps WHERE step_id=%s", (identity,)).fetchone()
    if existing:
        if existing["input_message_id"] != input_message_id:
            raise ValueError("A step cannot change its admitted input")
        return identity
    if path["status"] != "active":
        raise ValueError("A terminal Trigger Path cannot accept new work")
    if not created_at or (input_message_id is not None and kind != "resident"):
        raise ValueError("Invalid step admission")
    connection.execute(
        "INSERT INTO trigger_path_steps(step_id,trigger_path_id,node_id,kind,activation_key,input_message_id,"
        "status,created_at,checkpoint_json) VALUES(%s,%s,%s,%s,%s,%s,'pending',%s,%s)",
        (identity, trigger_path_id, node_id, kind, activation_key, input_message_id, created_at, encode(checkpoint or {})),
    )
    for predecessor, wire in predecessors:
        source = connection.execute("SELECT kind FROM trigger_path_steps WHERE step_id=%s AND trigger_path_id=%s",
                                    (predecessor, trigger_path_id)).fetchone()
        if source is None or source["kind"] == "resident":
            raise ValueError("Graph propagation must originate in this path and stops at a Resident")
        connection.execute("INSERT INTO trigger_path_step_edges VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                           (trigger_path_id, predecessor, identity, wire))
    _touch(connection, trigger_path_id)
    return identity


def transition_step(connection: Any, identity: str, *, status: str, at: str,
                    checkpoint: Mapping[str, Any] | None = None,
                    result: Mapping[str, Any] | None = None, input_status: str = "") -> None:
    row = connection.execute("SELECT trigger_path_id FROM trigger_path_steps WHERE step_id=%s", (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    path = _lock(connection, row["trigger_path_id"])
    row = connection.execute("SELECT * FROM trigger_path_steps WHERE step_id=%s", (identity,)).fetchone()
    if status not in STEP_TERMINAL | {"pending", "running", "waiting"} or not at:
        raise ValueError("Invalid step transition")
    if row["status"] in STEP_TERMINAL:
        if status != row["status"]:
            raise ValueError("A terminal step cannot be reopened")
        return
    if path["status"] != "active":
        raise ValueError("A terminal Trigger Path cannot advance")
    if row["kind"] == "resident" and status == "completed":
        # A committed handoff references its execution or a confirmed input
        # receipt. Pruning execution history does not erase that receipt.
        linked = connection.execute("SELECT 1 FROM trigger_path_execution_links WHERE step_id=%s LIMIT 1", (identity,)).fetchone()
        if linked is None and input_status != "completed":
            raise ValueError("A Resident handoff requires an Execution link")
    terminal = status in STEP_TERMINAL
    changes: dict[str, Any] = {"status": status}
    if status == "running" and row["started_at"] is None:
        changes["started_at"] = at
    if terminal:
        changes.update(finished_at=at, checkpoint_json="{}")
    elif checkpoint is not None:
        changes["checkpoint_json"] = encode(checkpoint)
    if result is not None:
        if row["kind"] == "resident" and result:
            raise ValueError("Resident output belongs to Agent execution history")
        changes["result_json"] = encode(result)
    connection.execute("UPDATE trigger_path_steps SET " + ",".join(key + "=%s" for key in changes) + " WHERE step_id=%s",
                       (*changes.values(), identity))
    _touch(connection, row["trigger_path_id"])


def link_execution(connection: Any, identity: str, execution_id: str) -> None:
    row = connection.execute("SELECT trigger_path_id FROM trigger_path_steps WHERE step_id=%s", (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    path = _lock(connection, row["trigger_path_id"])
    row = connection.execute("SELECT * FROM trigger_path_steps WHERE step_id=%s", (identity,)).fetchone()
    if row["kind"] != "resident" or not execution_id:
        raise ValueError("Only a Resident handoff can reference an Execution")
    if connection.execute("SELECT 1 FROM trigger_path_execution_links WHERE step_id=%s AND execution_id=%s",
                          (identity, execution_id)).fetchone():
        return
    if path["status"] != "active":
        raise ValueError("Terminal work cannot acquire another Execution")
    if row["status"] in STEP_TERMINAL:
        from management_portal.execution import input_continuations
        predecessors = connection.execute(
            "SELECT execution_id FROM trigger_path_execution_links WHERE step_id=%s", (identity,),
        ).fetchall()
        if row["status"] != "completed" or not input_continuations.permits_link(
            connection, predecessors=[r["execution_id"] for r in predecessors], execution_id=execution_id,
            resident_id=row["node_id"], message_id=row["input_message_id"],
        ):
            raise ValueError("Terminal work cannot acquire another Execution")
    connection.execute("INSERT INTO trigger_path_execution_links VALUES(%s,%s)", (identity, execution_id))
    _touch(connection, row["trigger_path_id"])


def recovery_lineage_ids(connection: Any) -> set[str]:
    # A terminal step keeps no checkpoint, so only open steps can be blocked. An
    # active path needs recovery while one of its open steps is not blocked, or
    # when none is open and only its Executions are left to settle.
    return {row["lineage_id"] for row in connection.execute(
        "SELECT p.lineage_id FROM trigger_path_steps s JOIN trigger_paths p USING(trigger_path_id) "
        "WHERE s.status IN ('pending','running','waiting') AND NOT s.checkpoint_json::jsonb ? 'recovery_blocked' "
        "AND p.status='active' UNION SELECT p.lineage_id FROM trigger_paths p WHERE p.status='active' "
        "AND NOT EXISTS (SELECT 1 FROM trigger_path_steps s WHERE s.trigger_path_id=p.trigger_path_id "
        "AND s.status IN ('pending','running','waiting'))"
    ).fetchall()}


def finish(connection: Any, identity: str, *, at: str, execution_statuses: Mapping[str, str],
           execution_finished_at: Mapping[str, Any] | None = None,
           input_states: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """Finish only after graph work and every linked execution settle.

    Callers obtain execution statuses and input receipts from their owners.
    A missing execution can be resolved by its exact input's terminal receipt.
    Unknown is pending, never inferred failure. Emitted events own other paths.
    """
    path = _lock(connection, identity)
    if path["status"] != "active":
        return path["status"]
    steps = connection.execute("SELECT status,finished_at FROM trigger_path_steps WHERE trigger_path_id=%s", (identity,)).fetchall()
    if any(row["status"] not in STEP_TERMINAL for row in steps):
        return "active"
    linked = connection.execute(
        "SELECT e.execution_id,e.step_id FROM trigger_path_execution_links e JOIN trigger_path_steps s USING(step_id) "
        "WHERE s.trigger_path_id=%s", (identity,),
    ).fetchall()
    input_states = input_states or {}
    statuses = [execution_statuses.get(row["execution_id"],
                input_states.get(row["step_id"], {}).get("state", "")) for row in linked]
    if any(value not in TERMINAL for value in statuses):
        return "active"
    statuses.extend(row["status"] for row in steps)
    status = "failed" if "failed" in statuses else "cancelled" if "cancelled" in statuses else "completed"
    times = [instant(row["finished_at"]) for row in steps if row["finished_at"]]
    times.extend(instant(value) for value in (execution_finished_at or {}).values() if value)
    times.extend(instant(value["updated_at"]) for value in input_states.values()
                 if value.get("state") in TERMINAL and value.get("updated_at"))
    finished_at = max(times) if times else instant(at)
    connection.execute("UPDATE trigger_paths SET status=%s,finished_at=%s,revision=revision+1 WHERE trigger_path_id=%s",
                       (status, finished_at, identity))
    return status


def cancel(connection: Any, identity: str, *, at: str) -> None:
    """Cancel graph work only. An independent/shared Execution is untouched."""
    path = _lock(connection, identity)
    if path["status"] != "active":
        return
    connection.execute("UPDATE trigger_path_steps SET status='cancelled',finished_at=%s,checkpoint_json='{}' "
                       "WHERE trigger_path_id=%s AND status IN ('pending','running','waiting')", (at, identity))
    connection.execute("UPDATE trigger_paths SET status='cancelled',finished_at=%s,revision=revision+1 WHERE trigger_path_id=%s",
                       (at, identity))


def read(connection: Any, identity: str) -> dict[str, Any] | None:
    path = connection.execute("SELECT * FROM trigger_paths WHERE trigger_path_id=%s", (identity,)).fetchone()
    if path is None:
        return None
    result = decode_times(path, ('occurred_at', 'finished_at'))
    result["input"] = json_value(result.pop("input_json"))
    result["steps"] = []
    for row in connection.execute("SELECT * FROM trigger_path_steps WHERE trigger_path_id=%s ORDER BY created_at,step_id", (identity,)).fetchall():
        step = decode_times(row, ('created_at', 'started_at', 'finished_at'))
        step["checkpoint"] = json_value(step.pop("checkpoint_json"))
        step["result"] = json_value(step.pop("result_json"))
        result["steps"].append(step)
    result["edges"] = [dict(row) for row in connection.execute("SELECT source_step_id,target_step_id,wire_id FROM trigger_path_step_edges "
                                                              "WHERE trigger_path_id=%s", (identity,)).fetchall()]
    result["execution_links"] = [dict(row) for row in connection.execute(
        "SELECT l.* FROM trigger_path_execution_links l JOIN trigger_path_steps s USING(step_id) WHERE s.trigger_path_id=%s",
        (identity,),
    ).fetchall()]
    return result

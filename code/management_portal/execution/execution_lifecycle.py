"""Commit scheduler admission/outcome with the authoritative Agent execution.

The caller supplies its transaction. Qualified Agent tables make the queue
change and execution fact atomic even though their owners use separate schemas.
Worker leases and retry checkpoints remain scheduler state.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from management_common.json_columns import json_value
from management_common.portal_database import _database_schema
from management_common.postgres_database import PostgresConnection

from .submission_identity import SubmissionIdentity


class _AgentTransaction(PostgresConnection):
    """Borrow the queue transaction without preparing SQL in its other schema."""

    def execute(self, statement, parameters=None):
        return self.raw.execute(statement, tuple(parameters or ()), prepare=False)


@contextmanager
def _agent_transaction(connection: Any):
    prior_path = connection.execute("SELECT current_setting('search_path')").fetchone()[0]
    try:
        with connection.raw.transaction():
            connection.execute("SELECT set_config('search_path',%s,true)", ('"' + _database_schema("agent") + '"',))
            yield _AgentTransaction(connection.raw)
    finally:
        connection.execute("SELECT set_config('search_path',%s,true)", (prior_path,))


def check_new_submission(connection: Any, execution_id: str, identity: SubmissionIdentity) -> None:
    """An expired queue receipt must never authorize re-executing a durable admission."""
    prefix = '"' + _database_schema("agent") + '".'
    existing = connection.execute(f"SELECT status FROM {prefix}executions WHERE execution_id=%s FOR UPDATE",
                                  (execution_id,)).fetchone()
    submission = connection.execute(f"SELECT data_json FROM {prefix}agent_operations WHERE operation_id=%s",
                                    ("execution_submission:" + execution_id,)).fetchone()
    if submission is not None:
        if not identity.matches(json_value(submission["data_json"])["request_digest"]):
            raise ValueError("execution id already exists with a different request")
        raise ValueError("Execution was already submitted; scheduler receipt expired; read execution.record")
    if existing is not None and existing["status"] != "preparing":
        raise ValueError("Execution already exists without an available admission receipt; read execution.record")


def reserve(connection: Any, *, execution_id: str, agent_id: str, actor: str, provider: str, created_at: str) -> None:
    """Use the admitting owner's transaction for the single execution identity."""
    if not all((execution_id, agent_id, actor, provider, created_at)):
        raise ValueError("Execution reservation requires identity and provenance")
    table = '"' + _database_schema("agent") + '".executions'
    connection.execute(
        f"INSERT INTO {table}(execution_id,agent_id,actor_ref,provider,provider_turn_id,created_at,"
        "started_at,finished_at,status,usage_json) VALUES(%s,%s,%s,%s,'',%s,NULL,NULL,'preparing','{}') "
        "ON CONFLICT(execution_id) DO NOTHING", (execution_id,agent_id,actor,provider,created_at),
    )
    row = connection.execute(f"SELECT agent_id,actor_ref,provider FROM {table} WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
    if (row["agent_id"],row["actor_ref"],row["provider"]) != (agent_id,actor,provider):
        raise ValueError("Execution identity refers to different immutable provenance")


def unstarted_handoff(connection: Any, execution_id: str, *, actor: str) -> dict:
    """Read a handoff even after scheduler retention, with original actor scope."""
    prefix = '"' + _database_schema("agent") + '".'
    row = connection.execute(
        f"SELECT e.actor_ref,o.data_json FROM {prefix}executions e "
        f"JOIN {prefix}agent_operations o ON o.operation_id=%s WHERE e.execution_id=%s",
        ("execution_handoff:" + execution_id, execution_id),
    ).fetchone()
    if row is None:
        return {}
    if row['actor_ref'] != actor:
        raise PermissionError("execution belongs to another actor")
    return json_value(row['data_json'])['receipt']


def retarget_preparation(connection: Any, *, execution_id: str, replacement_execution_id: str,
                         agent_id: str, actor: str, provider: str, at: str) -> bool:
    """Replace only an unsubmitted preparation; its old provenance stays immutable."""
    prefix = '"' + _database_schema("agent") + '".'
    row = connection.execute(
        f"SELECT agent_id,actor_ref,provider,status FROM {prefix}executions WHERE execution_id=%s FOR UPDATE",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise KeyError(execution_id)
    if (row['agent_id'], row['actor_ref']) != (agent_id, actor):
        raise ValueError("Execution identity refers to different immutable provenance")
    if row['provider'] == provider:
        return False
    admitted = connection.execute(f"SELECT 1 FROM {prefix}agent_operations WHERE operation_id=%s",
                                  ('execution_submission:' + execution_id,)).fetchone()
    if row['status'] != 'preparing' or admitted is not None:
        raise ValueError("An admitted Execution cannot change Provider during default binding")
    if not replacement_execution_id or replacement_execution_id == execution_id:
        raise ValueError("Provider binding requires a new preparation identity")
    connection.execute(f"UPDATE {prefix}executions SET status='cancelled',finished_at=%s WHERE execution_id=%s",
                       (at, execution_id))
    reserve(connection, execution_id=replacement_execution_id, agent_id=agent_id,
            actor=actor, provider=provider, created_at=at)
    record_unstarted_handoff(connection, agent_id=agent_id, performed_by=actor, at=at,
        receipt={'execution_id': execution_id, 'replacement_execution_id': replacement_execution_id,
                 'binding_id': 'resident:' + agent_id, 'operation_id': 'default-binding:' + execution_id})
    return True


def record_unstarted_handoff(connection: Any, *, agent_id: str, receipt: dict,
                             performed_by: str, at: str) -> None:
    from .agent_operations import put_operation
    with _agent_transaction(connection) as agent_connection:
        put_operation(agent_connection, operation_id="execution_handoff:" + receipt['execution_id'],
                      kind="execution_handoff", agent_id=agent_id, created_at=at,
                      data={"receipt": receipt, "performed_by": performed_by})


def scheduler_transition(connection: Any, execution_id: str, event_type: str, at: str) -> None:
    if event_type not in {"queued", "completed", "failed", "cancelled"}:
        return
    # Initialization is strict/versioned and cached by the domain owner.
    from .agent_data_store import get_agent_data_store
    get_agent_data_store()
    table = '"' + _database_schema("agent") + '".executions'
    row = connection.execute(
        "SELECT request_json,status,created_at,result_json FROM executions WHERE execution_id=%s",
        (execution_id,),
    ).fetchone()
    if row is None:
        raise KeyError(execution_id)
    request = json_value(row["request_json"])
    identity = (str(request["agent_id"]), str(request["actor"]), str(request["provider"]))
    connection.execute(
        f"INSERT INTO {table}(execution_id,agent_id,actor_ref,provider,provider_turn_id,created_at,"
        "started_at,finished_at,status,usage_json) VALUES(%s,%s,%s,%s,'',%s,NULL,NULL,'preparing','{}') "
        "ON CONFLICT(execution_id) DO NOTHING",
        (execution_id, *identity, row["created_at"]),
    )
    current = connection.execute(f"SELECT * FROM {table} WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
    if (current["agent_id"], current["actor_ref"], current["provider"]) != identity:
        raise ValueError("Execution identity refers to different immutable provenance")
    if event_type == "queued":
        from .contracts import ExecutionRequest
        digest = connection.execute("SELECT value FROM execution_store_meta WHERE key=%s",
                                    ("request_digest:" + execution_id,)).fetchone()
        if digest is None:
            raise ValueError("Execution admission requires its original request digest")
        with _agent_transaction(connection) as agent_connection:
            get_agent_data_store().record_execution_submission_in(
                agent_connection, ExecutionRequest.from_payload(request), request_digest=str(digest["value"]),
                submitted_at=at,
            )
        if current["status"] == "preparing":
            connection.execute(f"UPDATE {table} SET status='queued' WHERE execution_id=%s", (execution_id,))
        return
    if row["status"] != event_type:
        raise ValueError("Scheduler outcome does not match its committed state")
    if current["status"] in {"completed", "failed", "cancelled"}:
        if current["status"] != event_type:
            raise ValueError("Scheduler cannot replace a terminal Execution outcome")
        return
    result = json_value(row["result_json"] or "{}")
    store = get_agent_data_store()
    with _agent_transaction(connection) as agent_connection:
        store.finish_execution_in(agent_connection, execution_id, status=event_type,
                                  finished_at=result.get("finished_at") or at,
                                  usage=result.get("usage") or json_value(current["usage_json"] or "{}"))

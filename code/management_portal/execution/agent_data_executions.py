"""Executions: the record of each provider call, its accepted input and its final reply."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from management_common.database_time import instant, iso_time
from management_common.json_columns import json_value

from .agent_data_store import canonical_json, json_document, utc_now
from .agent_operations import put_operation
from .conversation_projection import CHAT_PREDICATE, CHAT_SEGMENT, REPLY_SEGMENT
from . import execution_inputs


SESSION_INPUT_ACCEPTANCE_SCHEMA = "tfi.session-input-acceptance.v1"


def _later_than(value: str, boundary: str) -> bool:
    """Whether an ISO timestamp is after another, whatever their precision."""

    try:
        return datetime.fromisoformat(value) > datetime.fromisoformat(boundary)
    except (TypeError, ValueError):
        return value > boundary


def _execution_record(row: Any) -> dict[str, Any]:
    return {
        "execution_id": str(row["execution_id"]),
        "agent_id": str(row["agent_id"]),
        "actor_ref": str(row["actor_ref"]),
        "provider": str(row["provider"]),
        "provider_session_ref": str(row["provider_session_ref"] or ""),
        "provider_turn_id": str(row["provider_turn_id"]),
        "created_at": (iso_time(row["created_at"]) or ""),
        "started_at": (iso_time(row["started_at"]) or ""),
        "finished_at": (iso_time(row["finished_at"]) or ""),
        "status": str(row["status"]),
        "usage": json_value(row["usage_json"] or "{}"),
    }


def record_execution_submission_in(connection: Any, request: Any, *, request_digest: str, submitted_at: str) -> None:
    """Persist validated request semantics in the admission transaction, without its body."""
    request.validate()
    if not re.fullmatch(r"[0-9a-f]{64}", request_digest):
        raise ValueError("Execution submission requires its original request digest")
    row = connection.execute("SELECT agent_id,actor_ref,provider FROM executions WHERE execution_id=%s FOR UPDATE",
                             (request.execution_id,)).fetchone()
    if row is None or (row["agent_id"], row["actor_ref"], row["provider"]) != (request.agent_id, request.actor, request.provider):
        raise ValueError("Execution submission does not match its immutable provenance")
    put_operation(connection, operation_id="execution_submission:" + request.execution_id,
                  kind="execution_submission", agent_id=request.agent_id, created_at=submitted_at,
                  data={"schema": "tfi.execution-submission.v1", "execution_id": request.execution_id,
                        "service_type": request.service_type, "operation": request.operation,
                        "request_digest": request_digest})


def _session_input_acceptance_id(session_ref: str, agent_id: str) -> str:
    digest = hashlib.sha256(canonical_json([session_ref, agent_id]).encode()).hexdigest()
    return f"segment_session_input_acceptance_{digest}"


def session_input_acceptance(
    connection: Any, session_ref: str, agent_id: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT data_json FROM agent_operations WHERE kind='input_accepted' AND session_ref=%s AND agent_id=%s ORDER BY created_at LIMIT 1",
        (session_ref, agent_id),
    ).fetchone()
    return json_value(row["data_json"]) if row else None


def record_session_input_acceptance(
    connection: Any, execution: Mapping[str, Any],
) -> bool:
    """Persist the first accepted turn independently of Execution retention."""
    session_ref = str(execution["provider_session_ref"] or "")
    turn_id = str(execution["provider_turn_id"] or "").strip()
    agent_id = str(execution["agent_id"])
    if not session_ref or not turn_id:
        return False
    if session_input_acceptance(connection, session_ref, agent_id) is not None:
        return False
    session = connection.execute(
        'SELECT provider FROM provider_sessions WHERE provider_session_ref = %s',
        (session_ref,),
    ).fetchone()
    if session is None or session["provider"] != execution["provider"]:
        raise ValueError("Session input acceptance requires the matching Provider Session")
    at = utc_now()
    put_operation(connection, operation_id=_session_input_acceptance_id(session_ref, agent_id),
        kind="input_accepted", agent_id=agent_id, session_ref=session_ref, created_at=at,
        data={"schema": SESSION_INPUT_ACCEPTANCE_SCHEMA, "provider_session_ref": session_ref,
              "agent_id": agent_id, "provider_turn_id": turn_id,
              "evidence_execution_id": str(execution["execution_id"]), "recorded_at": at})
    return True


def _final_reply_rows(connection: Any, executions: Sequence[str]) -> list[Any]:
    """The final reply of each terminal Execution, which its writes record (executions.final_message_id).

    It reads the trace itself, not its message projection: the write that
    ends an Execution holds the Execution and not its trace.
    """
    if not executions:
        return []
    # Resolve against the Execution's complete current trace, never the
    # requested page. A partial fork must not turn an inherited progress
    # message into the parent Execution's final reply.  A final answer
    # repeating a message earlier in the trace is not shown (SHOWN_POSITION).
    return connection.execute(
            "WITH selected_executions AS MATERIALIZED ("
            "SELECT execution_id,usage_json,status,trace_sequence_id FROM executions "
            "JOIN agents USING(agent_id) WHERE execution_id=ANY(%s) AND status IN ('completed','failed','cancelled')), "
            "replies AS ("
            "SELECT execution.*,segment.metadata_json,member.ordinal FROM selected_executions execution "
            "JOIN LATERAL (SELECT segment_id,metadata_json::jsonb AS metadata_json FROM segments segment "
            f"WHERE segment.execution_id=execution.execution_id AND segment.execution_id<>'' AND {CHAT_SEGMENT} OFFSET 0) segment ON TRUE "
            "JOIN LATERAL (SELECT ordinal FROM sequence_segments member "
            "WHERE member.sequence_id=execution.trace_sequence_id AND member.segment_id=segment.segment_id OFFSET 0) member ON TRUE "
            "WHERE segment.metadata_json->>'kind' IS DISTINCT FROM 'final_answer' OR NOT EXISTS ("
            "SELECT 1 FROM LATERAL (SELECT segment_id FROM segments candidate "
            f"WHERE {CHAT_PREDICATE} AND metadata_json::jsonb#>>'{{message,message_id}}'=segment.metadata_json#>>'{{message,message_id}}' "
            "OFFSET 0) previous JOIN sequence_segments earlier ON earlier.segment_id=previous.segment_id "
            "AND earlier.sequence_id=execution.trace_sequence_id AND earlier.ordinal<member.ordinal) "
            "UNION ALL SELECT execution.*, operation.data_json::jsonb->'metadata',position.ordinal "
            "FROM selected_executions execution JOIN operation_positions position "
            "ON position.sequence_id=execution.trace_sequence_id "
            "JOIN agent_operations operation USING(operation_id) "
            "WHERE operation.kind='notice' AND operation.data_json::jsonb->>'execution_id'=execution.execution_id) "
            "SELECT DISTINCT ON (execution_id) execution_id,usage_json, "
            "segment.metadata_json::jsonb#>>'{message,message_id}' AS message_id FROM replies segment "
            f"WHERE {REPLY_SEGMENT} "
            "AND (segment.metadata_json::jsonb#>>'{message,role}'='agent' OR "
            "segment.metadata_json::jsonb->>'kind'='portal_notice') "
            "ORDER BY execution_id, CASE WHEN status='completed' AND "
            "segment.metadata_json::jsonb#>>'{message,role}'='agent' THEN 0 "
            "WHEN status='completed' THEN 1 ELSE 0 END, ordinal DESC",
            (list(executions),),
        ).fetchall()


def refresh_final_reply(connection: Any, execution_id: str) -> None:
    if not execution_id:
        return
    terminal = connection.execute(
        "SELECT 1 FROM executions WHERE execution_id=%s AND status IN ('completed','failed','cancelled')",
        (execution_id,),
    ).fetchone()
    if terminal is None:
        return
    rows = _final_reply_rows(connection, [execution_id])
    if not rows:
        # Running Executions have no final reply. A terminal Execution may
        # legitimately have no displayed answer; the empty string records
        # that the projection was checked, unlike NULL (not backfilled).
        connection.execute(
            "UPDATE executions SET final_message_id='' WHERE execution_id=%s "
            "AND status IN ('completed','failed','cancelled') AND final_message_id IS DISTINCT FROM ''",
            (execution_id,),
        )
        return
    connection.execute(
        "UPDATE executions SET final_message_id=%s WHERE execution_id=%s "
        "AND status IN ('completed','failed','cancelled') AND final_message_id IS DISTINCT FROM %s",
        (str(rows[0]["message_id"] or ""), execution_id, str(rows[0]["message_id"] or "")),
    )


class ExecutionRecords:
    """Execution reservations, starts, provider facts and outcomes."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def reserve_execution(
        self, *, execution_id: str, agent_id: str, actor_ref: str, provider: str,
        created_at: str = "",
    ) -> dict[str, Any]:
        """Create the single execution identity before preparing its request.

        Preparation and pre-start cancellation are part of execution history.
        The queue and worker use this identity; they never allocate a second
        Resident job or turn identity.
        """
        if not all((execution_id, agent_id, actor_ref, provider)):
            raise ValueError("Execution reservation requires identity, agent, actor and provider")

        def write(connection: Any) -> dict[str, Any]:
            connection.execute(
                "INSERT INTO executions(execution_id,agent_id,actor_ref,provider,provider_session_ref,"
                "provider_turn_id,created_at,started_at,finished_at,status,usage_json) "
                "VALUES(%s,%s,%s,%s,NULL,'',%s,NULL,NULL,'preparing','{}') "
                "ON CONFLICT(execution_id) DO NOTHING",
                (execution_id, agent_id, actor_ref, provider, created_at or utc_now()),
            )
            row = connection.execute("SELECT * FROM executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if (row["agent_id"], row["actor_ref"], row["provider"]) != (agent_id, actor_ref, provider):
                raise ValueError("Execution identity refers to different immutable provenance")
            return _execution_record(row)

        return self.database.write(write)

    def queue_execution(self, execution_id: str) -> None:
        """Mark request admission; retries cannot rewind a started execution."""
        def write(connection: Any) -> None:
            row = connection.execute("SELECT status FROM executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if row is None:
                raise KeyError(execution_id)
            if row["status"] == "preparing":
                connection.execute("UPDATE executions SET status='queued' WHERE execution_id=%s", (execution_id,))
        self.database.write(write)

    def begin_execution(
        self,
        *,
        execution_id: str,
        agent_id: str,
        actor_ref: str,
        provider: str,
        started_at: str,
    ) -> dict[str, Any]:
        clean_execution_id = str(execution_id or "").strip()
        if not clean_execution_id:
            raise ValueError("Execution identity is required")
        identity = (
            str(agent_id or ""),
            str(actor_ref or ""),
            str(provider or ""),
            instant(started_at or utc_now()),
        )

        def write(connection: Any) -> dict[str, Any]:
            existing = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (clean_execution_id,),
            ).fetchone()
            if existing is not None:
                existing_identity = (
                    str(existing["agent_id"]),
                    str(existing["actor_ref"]),
                    str(existing["provider"]),
                )
                if existing_identity != identity[:3]:
                    raise ValueError(
                        "Execution identity refers to different immutable provenance"
                    )
                if existing["status"] in {"preparing", "queued"}:
                    connection.execute(
                        "UPDATE executions SET status='running',started_at=%s WHERE execution_id=%s",
                        (identity[3], clean_execution_id),
                    )
                    existing = connection.execute("SELECT * FROM executions WHERE execution_id=%s", (clean_execution_id,)).fetchone()
                elif existing["started_at"] != identity[3]:
                    raise ValueError("Execution identity refers to different immutable provenance")
                return _execution_record(existing)
            connection.execute(
                "INSERT INTO executions(execution_id,agent_id,actor_ref,provider,provider_session_ref,"
                "provider_turn_id,created_at,started_at,finished_at,status,usage_json) "
                "VALUES(%s,%s,%s,%s,NULL,'',%s,%s,NULL,'running','{}')",
                (
                    clean_execution_id,
                    identity[0],
                    identity[1],
                    identity[2],
                    identity[3],
                    identity[3],
                ),
            )
            created = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (clean_execution_id,),
            ).fetchone()
            if created is None:
                raise RuntimeError("Execution creation did not produce a record")
            return _execution_record(created)

        return self.database.write(write)

    def bind_execution_provider_session(
        self,
        execution_id: str,
        *,
        provider_session_ref: str,
    ) -> None:
        def write(connection: Any) -> None:
            execution = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (str(execution_id or ""),),
            ).fetchone()
            if execution is None:
                raise KeyError(execution_id)
            session = connection.execute(
                'SELECT provider FROM provider_sessions WHERE provider_session_ref = %s',
                (str(provider_session_ref or ""),),
            ).fetchone()
            if session is None:
                raise KeyError(provider_session_ref)
            if str(session["provider"]) != str(execution["provider"]):
                raise ValueError("Execution and Provider Session use different providers")
            current = str(execution["provider_session_ref"] or "")
            requested = str(provider_session_ref or "")
            if current and current != requested:
                raise ValueError("Execution is already bound to a different Provider Session")
            if not current:
                connection.execute(
                    'UPDATE executions SET provider_session_ref = %s WHERE execution_id = %s',
                    (requested, str(execution_id or "")),
                )
            record_session_input_acceptance(
                connection, {**dict(execution), "provider_session_ref": requested},
            )

        self.database.write(write)

    def record_input_acceptance(
        self, execution_id: str, *, agent_id: str, provider_turn_id: str,
    ) -> None:
        turn_id = str(provider_turn_id or "").strip()
        if not turn_id:
            raise ValueError("Provider input acceptance requires a turn identity")

        def write(connection: Any) -> None:
            execution = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
                (execution_id,),
            ).fetchone()
            if execution is None or execution["agent_id"] != agent_id:
                raise KeyError(execution_id)
            if execution["provider_turn_id"] == turn_id:
                record_session_input_acceptance(connection, execution)
                return
            if execution["status"] != "running":
                raise ValueError("Execution terminal fact cannot be changed")
            connection.execute(
                'UPDATE executions SET provider_turn_id = %s WHERE execution_id = %s',
                (turn_id, execution_id),
            )
            record_session_input_acceptance(
                connection, {**dict(execution), "provider_turn_id": turn_id},
            )

        self.database.write(write)

    def finish_execution(self, execution_id: str, *, status: str, finished_at: str = "",
                         usage: Mapping[str, Any] | None = None, provider_turn_id: str = "",
                         only_if_running: bool = False) -> bool:
        return self.database.write(lambda connection: self.finish_execution_in(
            connection, execution_id, status=status, finished_at=finished_at, usage=usage,
            provider_turn_id=provider_turn_id, only_if_running=only_if_running))

    def finish_execution_in(self, connection: Any, execution_id: str, *, status: str,
                            finished_at: str = "", usage: Mapping[str, Any] | None = None,
                            provider_turn_id: str = "", only_if_running: bool = False) -> bool:
        """Finalize in a transaction whose search path addresses Agent tables."""
        clean_status = str(status or "").strip()
        if clean_status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid terminal Execution status: {clean_status}")
        row = connection.execute(
            'SELECT * FROM executions WHERE execution_id = %s FOR UPDATE',
            (str(execution_id or ""),),
        ).fetchone()
        if row is None:
            raise KeyError(execution_id)
        desired_finished_at = instant(finished_at or row["finished_at"] or utc_now())
        desired_turn_id = str(provider_turn_id or row["provider_turn_id"] or "")
        desired_usage = json_document(dict(usage or {}))
        if only_if_running and row["status"] != "running":
            return False
        if str(row["status"]) in {"completed", "failed", "cancelled"}:
            # Maintenance enumerates running rows before reading Scheduler.
            # A Worker may have finalized this row in that interval.
            if only_if_running:
                return False
            reported = json_value(row["usage_json"] or "{}")
            # The Scheduler's count is added after the Worker's terminal fact.
            reported.pop("accounted", None)
            current = (
                str(row["status"]),
                row["finished_at"],
                str(row["provider_turn_id"]),
                reported,
            )
            desired = (
                clean_status,
                desired_finished_at,
                desired_turn_id,
                json_value(desired_usage),
            )
            if current != desired:
                raise ValueError("Execution terminal fact cannot be changed")
            record_session_input_acceptance(connection, row)
            if row["final_message_id"] is None:
                refresh_final_reply(connection, str(execution_id or ""))
            return False
        connection.execute(
            '\n                UPDATE executions\n                SET finished_at = %s, status = %s, usage_json = %s,\n                    provider_turn_id = %s\n                WHERE execution_id = %s\n                ',
            (
                desired_finished_at,
                clean_status,
                desired_usage,
                desired_turn_id,
                str(execution_id or ""),
            ),
        )
        record_session_input_acceptance(
            connection, {**dict(row), "provider_turn_id": desired_turn_id},
        )
        refresh_final_reply(connection, str(execution_id or ""))
        return True

    def record_execution_output(self, execution_id: str, *, usage: Mapping[str, Any] | None = None,
                                provider_turn_id: str = "") -> None:
        """Persist Provider output metadata; the scheduler owns the terminal outcome."""
        def write(connection):
            row = connection.execute("SELECT * FROM executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if row is None:
                raise KeyError(execution_id)
            if row["status"] in {"completed", "failed", "cancelled"}:
                return
            turn = provider_turn_id or row["provider_turn_id"]
            connection.execute("UPDATE executions SET usage_json=%s,provider_turn_id=%s WHERE execution_id=%s",
                               (json_document(dict(usage or {})),turn,execution_id))
            record_session_input_acceptance(connection,{**dict(row),"provider_turn_id":turn})
        self.database.write(write)

    def record_accounted_usage(self, execution_id: str, usage: Mapping[str, Any]) -> bool:
        """Keep what the Scheduler counted for an ended Execution's Provider requests.

        One Execution can run several Provider turns (a message sent into the
        running turn, a continuation, a Worker handover) and the Provider's own
        report covers only the last one; the Scheduler counted every request.
        """

        counted = {key: value for key, value in dict(usage or {}).items() if isinstance(value, int) and value > 0}
        if not counted:
            return False

        def write(connection: Any) -> bool:
            row = connection.execute(
                'SELECT status, usage_json FROM executions WHERE execution_id = %s FOR UPDATE',
                (str(execution_id or ""),),
            ).fetchone()
            if row is None or str(row["status"]) == "running":
                return False
            usage_payload = json_value(row["usage_json"] or "{}")
            if usage_payload.get("accounted") == counted:
                return False
            usage_payload["accounted"] = counted
            connection.execute(
                'UPDATE executions SET usage_json = %s WHERE execution_id = %s',
                (json_document(usage_payload), str(execution_id or "")),
            )
            return True

        return self.database.write(write)

    def provider_session_executions(self, provider: str, *, active_since: str) -> list[dict[str, Any]]:
        """The Provider sessions with an Execution running or ended since ``active_since``.

        Each session lists its route, when Portal recorded it, the bindings
        that use it and the time span of each of those Executions.
        """

        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT session.backend_session_id, session.backend_route_id, session.created_at AS session_created_at, "
                "execution.agent_id, execution.started_at, execution.finished_at, "
                "ARRAY(SELECT binding.binding_id FROM provider_bindings binding "
                "WHERE binding.provider_session_ref = session.provider_session_ref ORDER BY binding.binding_id) AS binding_ids "
                "FROM executions execution JOIN provider_sessions session USING (provider_session_ref) "
                "WHERE execution.provider = %s AND session.backend_session_id <> '' "
                "AND (execution.finished_at IS NULL OR execution.finished_at >= %s) "
                "ORDER BY session.backend_session_id, execution.started_at",
                (provider, active_since),
            ).fetchall()
        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            session = sessions.setdefault(str(row["backend_session_id"]), {
                "backend_session_id": str(row["backend_session_id"]),
                "backend_route_id": str(row["backend_route_id"] or ""),
                "agent_id": str(row["agent_id"]),
                "created_at": (iso_time(row["session_created_at"]) or ""),
                "binding_ids": [str(binding) for binding in row["binding_ids"] or ()],
                "executions": [],
            })
            session["executions"].append(((iso_time(row["started_at"]) or ""), (iso_time(row["finished_at"]) or "")))
        return list(sessions.values())

    def execution(self, execution_id: str) -> dict[str, Any] | None:
        with self.database.snapshot() as connection:
            row = connection.execute(
                'SELECT * FROM executions WHERE execution_id = %s',
                (str(execution_id or ""),),
            ).fetchone()
            if row is None:
                return None
            submission = connection.execute(
                "SELECT created_at,data_json FROM agent_operations WHERE operation_id=%s "
                "AND kind='execution_submission' AND agent_id=%s",
                ("execution_submission:" + str(execution_id), row["agent_id"]),
            ).fetchone()
            facts = json_value(submission["data_json"]) if submission is not None else {}
            return {**_execution_record(row),
                    "service_type": facts.get("service_type"), "operation": facts.get("operation"),
                    "submitted_at": iso_time(submission["created_at"]) if submission is not None else None,
                    "inputs": execution_inputs.read_in(connection, str(execution_id))}

    def binding_has_accepted_input(self, binding_id: str, *, agent_id: str) -> bool:
        with self.database.snapshot() as connection:
            row = connection.execute(
                'SELECT provider_session_ref FROM provider_bindings WHERE binding_id = %s',
                (binding_id,),
            ).fetchone()
            return row is not None and session_input_acceptance(
                connection, str(row["provider_session_ref"]), agent_id,
            ) is not None

    def running_executions(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM executions
                WHERE status = 'running'
                ORDER BY started_at, execution_id
                """
            ).fetchall()
        return [_execution_record(row) for row in rows]

    def latest_provider_session_ref(self, agent_id: str, *, since: str = "") -> str:
        """The Provider session of the agent's most recent Execution, if any.

        A session exists here as soon as the Provider accepted input, even when
        the turn failed before the session was bound.  With ``since`` (the time
        the agent's context was cleared) only an Execution started after it
        counts: the sessions before it hold the context that was cleared.
        """

        clean_agent_id = str(agent_id or "").strip()
        if not clean_agent_id:
            return ""
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT provider_session_ref, started_at FROM executions "
                "WHERE agent_id = %s AND provider_session_ref <> '' "
                "ORDER BY started_at DESC LIMIT 1",
                (clean_agent_id,),
            ).fetchone()
        if row is None:
            return ""
        if since and not _later_than((iso_time(row["started_at"]) or ""), since):
            return ""
        return str(row["provider_session_ref"])

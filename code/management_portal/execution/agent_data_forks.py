"""Forks: an Agent trace made from another's, and the promotion of a prepared session."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from management_common.database_time import iso_time
from management_common.json_columns import json_value

from .agent_data_chat import CHAT_COUNT_SQL, ChatReads, chat_floor, chat_rows
from .agent_data_contexts import AgentContexts
from .agent_data_executions import ExecutionRecords
from .agent_data_segments import existing_segment_ids, read_segments
from .agent_data_sessions import ProviderSessions
from .agent_data_store import canonical_json, stable_segment_id, utc_now
from .agent_data_trace_edits import TraceEdits
from .agent_data_traces import (
    agent_trace,
    append_sequence_ids,
    locked_trace,
    sequence_ids,
    trace_id,
)
from .agent_operations import put_operation


class AgentForks:
    """Fork creation, its frozen inheritance and initialization promotion."""

    def __init__(self, database: Any, *, executions: ExecutionRecords, sessions: ProviderSessions,
                 contexts: AgentContexts, trace_edits: TraceEdits, chat: ChatReads) -> None:
        self.database = database
        self.executions = executions
        self.sessions = sessions
        self.contexts = contexts
        self.trace_edits = trace_edits
        self.chat = chat

    def fork_agent(
        self, *, source_agent_id: str, target_agent_id: str,
        segment_ids: Sequence[str], actor_ref: str,
    ) -> dict[str, Any]:
        """Materialize the selected immutable references, never a parent's prefix.

        The creation owner freezes selection before publishing the child. Retries
        verify the same selection even after the child has started working.
        """
        source_id, target_id = str(source_agent_id).strip(), str(target_agent_id).strip()
        identities = list(segment_ids)
        if not source_id or not target_id or source_id == target_id:
            raise ValueError("Agent fork requires distinct source and target identities")
        if len(identities) != len(set(identities)):
            raise ValueError("Fork selection contains repeated Segment identities")

        def write(connection: Any) -> dict[str, Any]:
            if agent_trace(connection, source_id) is None:
                raise ValueError("Fork source Agent is not registered")
            target_sequence_id = locked_trace(connection, target_id, actor_ref)
            if len(existing_segment_ids(connection, identities)) != len(identities):
                raise ValueError("Fork selection contains unknown Segments")
            operation_id = stable_segment_id("fork", target_id)
            facts = {"source_agent_id": source_id, "segment_count": len(identities),
                     "selection_sha256": hashlib.sha256(canonical_json(identities).encode()).hexdigest()}
            receipt = connection.execute("SELECT operation_id FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone()
            if not receipt:
                existing = sequence_ids(connection, target_sequence_id)
                if existing and existing != identities:
                    raise ValueError("Fork target already contains different work")
                if not existing:
                    append_sequence_ids(connection, sequence={"sequence_id": target_sequence_id},
                                        segment_ids=identities, actor_ref=actor_ref)
            put_operation(connection, operation_id=operation_id, kind="forked", agent_id=target_id,
                          created_at=utc_now(), data=facts)
            return {"source_agent_id": source_id, "target_agent_id": target_id,
                    "trace_sequence_id": target_sequence_id, "segment_count": len(identities)}

        return self.database.write(write)

    def frozen_fork_segments(self, agent_id: str) -> list[str]:
        """Verify the original Fork prefix, never reconstruct it from today's context."""
        with self.database.snapshot() as c:
            row = c.execute("SELECT data_json FROM agent_operations WHERE operation_id=%s AND kind='forked'",
                            (stable_segment_id('fork', agent_id),)).fetchone()
            if row is None:
                raise ValueError('初始化恢复缺少原 Fork 冻结凭据')
            facts = json_value(row['data_json'])
            ids = [str(r['segment_id']) for r in c.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal LIMIT %s',
                (trace_id(c, agent_id), int(facts.get('segment_count') or 0)),).fetchall()]
            if hashlib.sha256(canonical_json(ids).encode()).hexdigest() != facts.get('selection_sha256'):
                raise ValueError('原 Fork 冻结片段已变化，不能猜测恢复上下文')
            return ids

    def frozen_fork_references(self, agent_id: str) -> list[dict[str, str]]:
        with self.database.snapshot() as c:
            ids = self.frozen_fork_segments(agent_id)
            turns = {str(r['segment_id']): str(r['provider_turn_id'] or '') for r in c.execute(
                'SELECT segment_id,provider_turn_id FROM segments LEFT JOIN executions USING(execution_id) WHERE segment_id=ANY(%s)',
                (ids,),).fetchall()}
            return [{'segment_id': sid, 'provider_turn_id': turns[sid]} for sid in ids]

    def initialization_session_fact(self, agent_id: str, binding_id: str) -> dict[str, Any]:
        """Canonical facts only; a failed/expired Execution is not a native exit."""
        binding = self.sessions.provider_binding(binding_id)
        head = self.contexts.session_context_head(str(binding.get('provider_session_ref') or ''), agent_id=agent_id) if binding else None
        execution = self.executions.execution(str((head or {}).get('execution_id') or '')) if head else None
        with self.database.read() as c:
            active = [str(r['execution_id']) for r in c.execute(
                "SELECT execution_id FROM executions WHERE agent_id=%s AND status IN ('queued','running')",
                (agent_id,),).fetchall()]
            receipt = c.execute("SELECT data_json FROM agent_operations WHERE agent_id=%s AND kind='initialization_promoted' ORDER BY created_at DESC LIMIT 1",
                                (agent_id,)).fetchone()
        return {'binding': binding, 'active_execution_ids': active,
                'compaction_segment_id': str(head['segment_id']) if head and execution and execution['status'] == 'completed'
                    and execution['agent_id'] == agent_id and execution['provider_session_ref'] == binding.get('provider_session_ref') else '',
                'receipt': json_value(receipt['data_json']) if receipt else {}}

    def promote_initialization_session(self, *, agent_id: str, candidate_agent_id: str,
                                      binding_id: str, prepared_binding_id: str,
                                      source_binding: Mapping[str, Any], segment_ids: list[str],
                                      compaction_execution_id: str, initialization_id: str,
                                      actor_ref: str) -> dict[str, Any]:
        """Promote the binding and canonical compaction together; keep the old Session audit."""
        operation_id = 'initialization_promoted:' + initialization_id
        def write(c: Any) -> dict[str, Any]:
            trace_id = locked_trace(c, agent_id, actor_ref)
            replay = c.execute('SELECT data_json FROM agent_operations WHERE operation_id=%s', (operation_id,)).fetchone()
            if replay:
                return json_value(replay['data_json'])
            c.execute('SELECT binding_id FROM provider_bindings WHERE binding_id=ANY(%s) ORDER BY binding_id FOR UPDATE',
                      ([binding_id, prepared_binding_id],)).fetchall()
            candidate = self.sessions.provider_binding(prepared_binding_id)
            execution = self.executions.execution(compaction_execution_id)
            head = self.contexts.session_context_head(str(candidate.get('provider_session_ref') or ''), agent_id=candidate_agent_id)
            if not execution or execution['status'] != 'completed' or execution['agent_id'] != candidate_agent_id or execution['provider_session_ref'] != candidate.get('provider_session_ref') or not head or head.get('execution_id') != compaction_execution_id:
                raise ValueError('候选会话尚无已完成的原生压缩事实')
            current = [str(r['segment_id']) for r in c.execute(
                'SELECT segment_id FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal', (trace_id,)).fetchall()]
            if current[:len(segment_ids)] != segment_ids:
                raise ValueError('恢复期间继承上下文发生变化')
            replacement = self.sessions.replace_provider_binding(binding_id, prepared_binding_id,
                expected_provider_session_ref=str(source_binding.get('provider_session_ref') or ''),
                expected_absent=not source_binding)
            compact_id = str(head['segment_id'])
            self.trace_edits.append_agent_segments(agent_id, [compact_id], actor_ref=actor_ref)
            self.trace_edits.place_in_trace_before(agent_id, compact_id, current[len(segment_ids):], actor_ref=actor_ref)
            facts = {'initialization_id': initialization_id, 'candidate_agent_id': candidate_agent_id,
                     'source_session': dict(source_binding), 'target_session': replacement['current_binding'],
                     'compaction_execution_id': compaction_execution_id, 'compaction_segment_id': compact_id}
            put_operation(c, operation_id=operation_id, kind='initialization_promoted', agent_id=agent_id,
                          created_at=utc_now(), data=facts)
            return facts
        return self.database.write(write)

    def fork_inheritance(self, agent_id: str, *, include_compaction: bool = False) -> dict[str, Any]:
        """What a Fork's trace took from its source when it was created.

        The forked receipt fixes how many leading trace members were inherited.
        The result names the opening compaction (its content only on request),
        the inherited Chat messages, and whether a later context reset hides
        them. An Agent that no Fork created has no receipt and gets ``{}``.
        """
        agent_id = str(agent_id or "").strip()
        self.chat.chat_traces([agent_id])
        with self.database.snapshot() as connection:
            receipt = connection.execute(
                "SELECT data_json FROM agent_operations WHERE operation_id=%s AND kind='forked'",
                (stable_segment_id("fork", agent_id),),
            ).fetchone()
            sequence_id = trace_id(connection, agent_id)
            if receipt is None or not sequence_id:
                return {}
            facts = json_value(receipt["data_json"])
            count = int(facts.get("segment_count") or 0)
            result: dict[str, Any] = {
                "source_agent_id": str(facts.get("source_agent_id") or ""), "segment_count": count,
                "opening_compaction": None, "message_count": 0, "last_message_id": "", "context_cleared": False,
            }
            if not count:
                return result
            first = connection.execute(
                "SELECT m.ordinal, s.segment_id, s.segment_type, COALESCE(s.finished_at,s.started_at) AS created_at FROM sequence_segments m "
                "JOIN segments s USING(segment_id) WHERE m.sequence_id=%s ORDER BY m.ordinal LIMIT 1",
                (sequence_id,),
            ).fetchone()
            last = connection.execute(
                "SELECT ordinal FROM sequence_segments WHERE sequence_id=%s ORDER BY ordinal OFFSET %s LIMIT 1",
                (sequence_id, count - 1),
            ).fetchone()
            if first is None or last is None:
                return result
            boundary = int(last["ordinal"])
            if first["segment_type"] == "compaction":
                compaction = {"segment_id": str(first["segment_id"]), "created_at": (iso_time(first["created_at"]) or "")}
                if include_compaction:
                    compaction["content"] = read_segments(connection, [compaction["segment_id"]])[0]["content"]
                result["opening_compaction"] = compaction
            counted = connection.execute(CHAT_COUNT_SQL, (sequence_id, boundary)).fetchone()
            result["message_count"] = int(counted["count"])
            latest = chat_rows(connection, sequence_id, before=boundary + 1, limit=1)
            if latest:
                message = json_value(latest[-1]["metadata_json"]).get("message") or {}
                result["last_message_id"] = str(message.get("message_id") or "")
            # The Chat shows only what follows its last context reset.
            result["context_cleared"] = chat_floor(connection, sequence_id) >= boundary
            return result

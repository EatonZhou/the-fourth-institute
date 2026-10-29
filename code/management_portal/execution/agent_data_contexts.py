"""What a Provider session and an Agent hold as model context, read from the Agent trace."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from management_common.database_time import iso_time
from management_common.json_columns import json_value

from .agent_data_chat import ChatReads, chat_member
from .agent_data_executions import ExecutionRecords
from .agent_data_segments import SegmentRecords, read_segment_members, read_segments
from .agent_data_store import in_batches, stable_segment_id, utc_now
from .agent_data_traces import (
    PROVIDER_TURN_SQL,
    agent_trace,
    locked_trace,
    place_operation,
    trace_id,
    trace_rows,
)
from .agent_operations import put_operation


# The trace mark of what a Provider session's context began with.
CONTEXT_SEED_KIND = "context_seed"
# A reminder of the Harness loop governor that reached a running turn.
HARNESS_CONTROL_KIND = "harness_control"
# Historical Provider window facts remain readable in existing traces: its
# reminders, attached files, base instructions, settings and the model's own
# tool calls.  A session's context holds them; carried to another Provider
# (a Fork, a seed) they stay behind.
PROVIDER_INJECTED_KIND = "provider_injected"
PROVIDER_TOOL_CALL_KIND = "provider_tool_call"
PROVIDER_ONLY_KINDS = frozenset({PROVIDER_INJECTED_KIND, PROVIDER_TOOL_CALL_KIND})


CONTEXT_EVIDENCE_POSITION_LIMIT = 2000


def _session_trace_owners(connection: Any, session_ref: str, agent_id: str = "") -> Iterator[str]:
    """Agents whose trace may hold a Provider session's work, most likely first.

    The given Agent, then the Agents that recorded the session's work,
    then the Agents whose traces hold it (a session restart's work was
    merged into its Resident's trace; a Fork's trace holds a copy, the
    original holds the most of it and is the older one), then the
    Residents bound to it (a session nothing worked in yet holds only the
    context its Resident's trace marks).  Each is read only when the ones
    before it did not do.
    """

    seen: set[str] = set()

    def fresh(owners: Iterable[str]) -> Iterator[str]:
        for owner in owners:
            if owner and owner not in seen:
                seen.add(owner)
                yield owner

    yield from fresh([str(agent_id or "").strip()])
    yield from fresh(
        str(row["created_by_agent_id"])
        for row in connection.execute(
            "SELECT segment.created_by_agent_id, COUNT(*) AS members FROM executions execution "
            "JOIN segments segment ON segment.execution_id = execution.execution_id "
            "WHERE execution.provider_session_ref = %s AND segment.execution_id <> '' "
            "GROUP BY 1 ORDER BY 2 DESC",
            (session_ref,),
        ).fetchall()
    )
    yield from fresh(
        str(row["agent_id"])
        for row in connection.execute(
            "SELECT agent.agent_id, COUNT(*) AS members, MIN(sequence.created_at) AS created_at "
            "FROM (SELECT segment.segment_id FROM executions execution "
            "JOIN segments segment ON segment.execution_id = execution.execution_id "
            "WHERE execution.provider_session_ref = %s AND segment.execution_id <> '' LIMIT 500) own "
            "JOIN sequence_segments member ON member.segment_id = own.segment_id "
            "JOIN sequences sequence ON sequence.sequence_id = member.sequence_id "
            "JOIN agents agent ON agent.trace_sequence_id = sequence.sequence_id AND agent.sequence_kind = 'agent_trace' "
            "GROUP BY agent.agent_id ORDER BY members DESC, created_at",
            (session_ref,),
        ).fetchall()
    )
    yield from fresh(
        str(row["binding_id"]).split(":", 1)[1]
        for row in connection.execute(
            "SELECT binding_id FROM provider_bindings WHERE provider_session_ref = %s "
            "AND binding_id LIKE 'resident:%%'",
            (session_ref,),
        ).fetchall()
    )


def _session_window(connection: Any, session_ref: str, agent_id: str) -> tuple[str, int, int] | None:
    for owner in _session_trace_owners(connection, session_ref, agent_id):
        sequence_id = trace_id(connection, owner)
        if not sequence_id:
            continue
        own = connection.execute(
            "WITH session_executions AS MATERIALIZED ("
            "SELECT execution_id FROM executions WHERE provider_session_ref=%s) "
            "SELECT MIN(member.ordinal) AS first, MAX(member.ordinal) AS last "
            "FROM session_executions execution JOIN segments segment "
            "ON segment.execution_id=execution.execution_id AND segment.execution_id<>'' "
            "JOIN sequence_segments member ON member.segment_id=segment.segment_id "
            "WHERE member.sequence_id=%s", (session_ref, sequence_id),
        ).fetchone()
        delivery = connection.execute(
            "SELECT MIN(position.ordinal) AS first, MAX(last_member.ordinal) AS last "
            "FROM operation_positions position JOIN agent_operations operation USING(operation_id) "
            "LEFT JOIN sequence_segments last_member ON last_member.sequence_id=position.sequence_id "
            "AND last_member.segment_id=operation.data_json::jsonb->>'last_segment_id' "
            "WHERE position.sequence_id=%s AND operation.session_ref=%s AND operation.kind='context_received'",
            (sequence_id, session_ref),
        ).fetchone()
        starts = [int(row["first"]) for row in (own, delivery) if row["first"] is not None]
        ends = [int(row["last"]) for row in (own, delivery) if row["last"] is not None]
        if starts and ends:
            return sequence_id, min(starts), max(ends)
    return None


def session_transcript_ids(connection: Any, session_ref: str, *, agent_id: str = "") -> list[str]:
    window = _session_window(connection, session_ref, agent_id)
    if not window:
        return []
    sequence_id, first, last = window
    return [str(row["segment_id"]) for row in connection.execute(
        "SELECT segment_id FROM sequence_segments WHERE sequence_id=%s AND ordinal BETWEEN %s AND %s ORDER BY ordinal",
        (sequence_id, first, last),
    ).fetchall()]


def read_session_context_ids(connection: Any, session_ref: str, *, agent_id: str = "") -> list[str]:
    window = _session_window(connection, session_ref, agent_id)
    if not window:
        return []
    sequence_id, first, last = window
    ids: list[str] = []
    before = last + 1
    while True:
        page = trace_rows(connection, sequence_id, first=first, before=before, newest_first=True, limit=256)
        for row in page:
            ids.append(str(row["segment_id"]))
            if row["segment_type"] == "compaction":
                return list(reversed(ids))
        if len(page) < 256:
            return list(reversed(ids))
        before = int(page[-1]["ordinal"])


def _context_seed_id(session_ref: str, agent_id: str, segment_ids: Sequence[str]) -> str:
    listed = hashlib.sha256("\0".join(str(item) for item in segment_ids).encode("utf-8")).hexdigest()
    return stable_segment_id(CONTEXT_SEED_KIND, session_ref, agent_id, listed)


def mark_context_received(
    connection: Any, agent_id: str, session_ref: str, segment_ids: Sequence[str],
    *, actor_ref: str,
) -> str:
    if not agent_id or not session_ref:
        raise ValueError("Context delivery needs an Agent and Provider session")
    if not segment_ids:
        return ""
    sequence_id = locked_trace(connection, agent_id, actor_ref)
    rows = connection.execute(
        "SELECT segment_id, ordinal FROM sequence_segments WHERE sequence_id=%s AND segment_id=ANY(%s) ORDER BY ordinal",
        (sequence_id, list(segment_ids)),
    ).fetchall()
    present = {str(row["segment_id"]) for row in rows}
    missing = [item for item in segment_ids if item not in present]
    if missing:
        raise ValueError("Received context is not present in the Agent trace")
    operation_id = _context_seed_id(session_ref, agent_id, segment_ids)
    put_operation(connection, operation_id=operation_id, kind="context_received", agent_id=agent_id,
        session_ref=session_ref, created_at=utc_now(),
        data={"first_segment_id": str(rows[0]["segment_id"]), "last_segment_id": str(rows[-1]["segment_id"])})
    place_operation(connection, sequence_id, operation_id, int(rows[0]["ordinal"]), actor_ref)
    return operation_id


def attach_provider_turns(connection, segments):
    identities = [item["segment_id"] for item in segments]
    turns = {}
    for chunk in in_batches(identities):
        turns.update({str(row["segment_id"]): str(row["provider_turn_id"] or "")
            for row in connection.execute(
                f"SELECT segment.segment_id, {PROVIDER_TURN_SQL} AS provider_turn_id "
                "FROM segments segment LEFT JOIN executions execution USING(execution_id) "
                "WHERE segment.segment_id = ANY(%s)", (chunk,),
            ).fetchall()})
    for item in segments:
        item["provider_turn_id"] = turns[item["segment_id"]]


def read_agent_context(connection: Any, agent_id: str, *, before_message_id: str = "", through_message_id: str = "", through_turn_id: str = "", provider_session_ref: str = "") -> dict[str, Any]:
    trace = agent_trace(connection, agent_id)
    if trace is None:
        raise KeyError(f"Agent trace not found: {agent_id}")
    sequence_id = str(trace["sequence_id"])
    first = 1
    before = None
    if provider_session_ref:
        session_owner = connection.execute(
            "SELECT 1 FROM executions WHERE provider_session_ref=%s AND agent_id=%s "
            "UNION ALL SELECT 1 FROM agent_operations WHERE session_ref=%s AND agent_id=%s "
            "AND kind='context_received' LIMIT 1",
            (provider_session_ref, agent_id, provider_session_ref, agent_id),
        ).fetchone()
        # Inherited messages retain their original session provenance. Its
        # delivery receipts belong to the source, not this independent trace.
        window = _session_window(connection, provider_session_ref, agent_id) if (
            session_owner is not None
        ) else None
        if window and window[0] == sequence_id:
            first = max(first, window[1])
            before = window[2] + 1
    if before_message_id:
        member = chat_member(connection, sequence_id, before_message_id)
        if member is None:
            raise ValueError("Context input boundary is not in the Agent trace")
        before = int(member["ordinal"])
    if through_message_id:
        member = chat_member(connection, sequence_id, through_message_id)
        if member is None:
            raise ValueError("Fork cutoff message is not in the Agent trace")
        # The selected input and following responses form a trace interval.
        # A recorded Provider turn can group appended inputs, but old traces
        # remain usable without any Execution or Provider session record.
        targets = trace_rows(connection, sequence_id, first=int(member["ordinal"]), limit=1)
        if not targets:
            # A notice shown after the trace's last segment, such as an empty reply.
            raise ValueError("Fork cutoff message is not in the Agent trace")
        target = targets[0]
        target_turn = str(target["provider_turn_id"] or "")
        # Use the same turn boundary as recent Fork selection. Held-info
        # Segments carry released_with and stay with the carrier's answers;
        # an absent Provider receipt alone still starts a genuine user turn.
        from .context_seed import is_context_turn_input

        next_ordinal = int(member["ordinal"]) + 1
        before = None
        while True:
            page = trace_rows(connection, sequence_id, first=next_ordinal, limit=256)
            for row in page:
                item = {"segment_type": row["segment_type"],
                        "metadata": json_value(row["metadata_json"])}
                if row["segment_type"] == "compaction" or (
                    is_context_turn_input(item)
                    and (not target_turn or str(row["provider_turn_id"] or "") != target_turn)
                ):
                    before = int(row["ordinal"])
                    break
            if before is not None or len(page) < 256:
                break
            next_ordinal = int(page[-1]["ordinal"]) + 1
        reset = connection.execute(
            "SELECT MIN(p.ordinal) AS ordinal FROM operation_positions p JOIN agent_operations o USING(operation_id) "
            "WHERE p.sequence_id=%s AND p.ordinal>%s AND o.kind IN ('context_reset','provider_context_reset')",
            (sequence_id, member["ordinal"]),
        ).fetchone()
        if reset["ordinal"] is not None:
            before = min(before, int(reset["ordinal"])) if before is not None else int(reset["ordinal"])
    if through_turn_id:
        cutoff = connection.execute(
            "SELECT MAX(m.ordinal) AS ordinal FROM sequence_segments m JOIN segments segment USING(segment_id) "
            "LEFT JOIN executions execution USING(execution_id) WHERE m.sequence_id=%s "
            f"AND {PROVIDER_TURN_SQL}=%s",
            (sequence_id, through_turn_id),
        ).fetchone()
        if cutoff["ordinal"] is None:
            raise ValueError("Fork cutoff has no recorded context in this Agent trace")
        before = int(cutoff["ordinal"]) + 1
    boundary = connection.execute(
        "SELECT COALESCE(MAX(p.ordinal),0) AS ordinal FROM operation_positions p "
        "JOIN agent_operations o USING(operation_id) WHERE p.sequence_id=%s "
        "AND o.kind IN ('context_reset','provider_context_reset') "
        "AND (%s::bigint IS NULL OR p.ordinal < %s)", (sequence_id, before, before),
    ).fetchone()
    first = max(first, int(boundary["ordinal"]) + 1)
    rows = []
    while True:
        page = trace_rows(connection, sequence_id, first=first, before=before,
                          newest_first=True, limit=256)
        stop = False
        for row in page:
            rows.append(row)
            if row["segment_type"] == "compaction":
                stop = True
                break
        if stop or len(page) < 256:
            break
        before = int(page[-1]["ordinal"])
    rows.reverse()
    selected = []
    for row in rows:
        metadata = json_value(row["metadata_json"])
        if metadata.get("kind") in PROVIDER_ONLY_KINDS | {"system_instructions"}:
            continue
        selected.append(row)
    segments = read_segments(connection, [str(row["segment_id"]) for row in selected])
    for item, row in zip(segments, selected):
        item["provider_turn_id"] = str(row["provider_turn_id"] or "")
    return {"segments": segments, "empty_context": not segments}


class AgentContexts:
    """Context snapshots, session context heads and the marks of received context."""

    def __init__(self, database: Any, *, segments: SegmentRecords, executions: ExecutionRecords,
                 chat: ChatReads) -> None:
        self.database = database
        self.segments = segments
        self.executions = executions
        self.chat = chat

    def agent_context_snapshot(self, agent_id: str, *, before_message_id: str = "", through_message_id: str = "", through_turn_id: str = "", provider_session_ref: str = "") -> dict[str, Any]:
        """The Agent's own effective window, including a Fork with no session yet.

        Walk backwards to the last compaction/reset. Read payloads only for the
        retained window. Execution receipts do not determine trace membership.
        Delivery excludes its current input with before_message_id.
        """
        if before_message_id or through_message_id:
            # The boundary messages are found by the projection (chat_member).
            self.chat.chat_traces([agent_id])
        with self.database.snapshot() as connection:
            return read_agent_context(connection, agent_id, before_message_id=before_message_id, through_message_id=through_message_id, through_turn_id=through_turn_id, provider_session_ref=provider_session_ref)

    def context_evidence_snapshot(self, agent_id: str) -> dict[str, Any]:
        """Read audit counts, effective context and bounded import facts atomically.

        This is an explicit diagnostic read. Normal conversation reads do not
        load model context. Its owner must authorize the Agent before calling.
        """
        with self.database.snapshot() as connection:
            observation = connection.execute(
                "SELECT clock_timestamp() AS sampled_at, "
                "current_setting('transaction_isolation') AS isolation, "
                "current_setting('transaction_read_only') AS read_only"
            ).fetchone()
            if observation["isolation"] != "repeatable read" or observation["read_only"] != "on":
                raise RuntimeError("Context evidence requires a read-only repeatable-read snapshot")
            trace = agent_trace(connection, agent_id)
            positions = {"schema": "tfi.trace-position-evidence.v1",
                         "sampled_at": iso_time(observation["sampled_at"]),
                         "consistency": "repeatable_read_read_only",
                         "trace_id": trace["sequence_id"] if trace else "",
                         "trace_updated_at": iso_time(trace["updated_at"]) if trace else "",
                         "entries": [], "truncated": False}
            if trace is None:
                return {"trace_counts": {}, "segments": [], "imports": [], "imports_truncated": False,
                        "trace_positions": positions, "transformations": [], "transformations_truncated": False}
            rows = connection.execute(
                "SELECT member.ordinal, member.segment_id, segment.segment_type, segment.content_sha256, "
                "segment.execution_id, segment.started_at AS segment_started_at, "
                "segment.finished_at AS segment_finished_at, "
                "segment.metadata_json::jsonb#>>'{message,message_id}' AS message_id, "
                "segment.metadata_json::jsonb#>>'{provider_observation,item_id}' AS provider_item_id, "
                f"{PROVIDER_TURN_SQL} AS provider_turn_id, execution.provider_session_ref, "
                "session.provider, session.backend_route_id, session.backend_session_id "
                "FROM sequence_segments member JOIN segments segment USING(segment_id) "
                "LEFT JOIN executions execution ON execution.execution_id=segment.execution_id "
                "LEFT JOIN provider_sessions session USING(provider_session_ref) "
                "WHERE member.sequence_id=%s ORDER BY member.ordinal LIMIT %s",
                (trace["sequence_id"], CONTEXT_EVIDENCE_POSITION_LIMIT + 1),
            ).fetchall()
            positions["entries"] = [
                {key: (int(value) if key == "ordinal" else iso_time(value) if key in
                       {"segment_started_at", "segment_finished_at"} else value or "")
                 for key, value in dict(row).items()} for row in rows[:CONTEXT_EVIDENCE_POSITION_LIMIT]]
            positions["truncated"] = len(rows) > CONTEXT_EVIDENCE_POSITION_LIMIT
            transformations = connection.execute(
                "SELECT operation_id, data_json FROM agent_operations "
                "WHERE session_ref='' AND agent_id=%s AND kind='trace_transformation' "
                "ORDER BY created_at DESC, operation_id DESC LIMIT %s",
                (agent_id, CONTEXT_EVIDENCE_POSITION_LIMIT + 1),
            ).fetchall()
            counts = connection.execute(
                "SELECT s.segment_type, COUNT(*) AS count FROM sequence_segments m "
                "JOIN segments s USING(segment_id) WHERE m.sequence_id=%s GROUP BY s.segment_type",
                (trace["sequence_id"],),
            ).fetchall()
            context = read_agent_context(connection, agent_id)
            compaction_ids = [item["segment_id"] for item in context["segments"]
                              if item["segment_type"] == "compaction"]
            compressed_from = [dict(row) for row in connection.execute(
                "SELECT source_segment_id,target_segment_id FROM segment_relations "
                "WHERE relation_type='compressed_from' AND target_segment_id=ANY(%s) "
                "ORDER BY target_segment_id,source_segment_id LIMIT 2001", (compaction_ids,),
            ).fetchall()] if compaction_ids else []
            rows = connection.execute(
                "SELECT operation_id, data_json FROM agent_operations WHERE agent_id=%s "
                "AND kind='artifact' AND starts_with(operation_id, 'segment_fork_import_') "
                "ORDER BY created_at DESC, operation_id DESC LIMIT 21", (agent_id,),
            ).fetchall()
            imports = []
            for row in rows[:20]:
                record = json_value(row["data_json"])
                if record.get("metadata", {}).get("kind") != "fork_history_delivery":
                    continue
                imports.append({"operation_id": row["operation_id"], "content": record["content"]})
            identities = list(dict.fromkeys(
                str(ref["segment_id"]) for item in imports for ref in item["content"]["segments"]
            ))
            segments = {item["segment_id"]: item for item in read_segments(connection, identities)}
            for item in imports:
                item["segments"] = [
                    {**segments[ref["segment_id"]], "provider_turn_id": str(ref.get("provider_turn_id") or "")}
                    for ref in item["content"]["segments"]
                ]
            return {"trace_counts": {row["segment_type"]: int(row["count"]) for row in counts},
                    "segments": context["segments"], "imports": imports,
                    "compressed_from": compressed_from[:2000],
                    "compressed_from_truncated": len(compressed_from) > 2000,
                    "trace_positions": positions,
                    "transformations": [{"receipt_id": row["operation_id"], **json_value(row["data_json"])}
                                        for row in transformations[:CONTEXT_EVIDENCE_POSITION_LIMIT]],
                    "transformations_truncated": len(transformations) > CONTEXT_EVIDENCE_POSITION_LIMIT,
                    "imports_truncated": len(rows) > 20}

    def latest_context_compaction(self, agent_id: str, *, since: str = "") -> dict[str, Any] | None:
        """The compaction that heads an agent's newest Provider context.

        The newest context is that of the Provider session of the agent's most
        recent Execution (after ``since``, as for ``latest_provider_session_ref``),
        read from the trace.  Returns that compaction Segment, or ``None`` when
        the context has not been compacted.
        """

        provider_session_ref = self.executions.latest_provider_session_ref(agent_id, since=since)
        if not provider_session_ref:
            return None
        return self.session_context_head(provider_session_ref, agent_id=agent_id)

    def message_in_context(self, agent_id: str, message_id: str, *, since: str = "") -> bool:
        """Whether a Chat message the Provider took is still in the agent's newest context.

        The newest context is the one ``latest_context_compaction`` reads: the
        Provider session of the agent's most recent Execution after ``since``,
        from its last compaction on.  A message no Provider took, one a clear
        hid, one an earlier session took and one a compaction has since
        summarised are not in it.
        """

        try:
            source = self.chat.chat_message_provenance(agent_id, message_id)
        except KeyError:
            return False
        # A fresh restart clears the canonical context before the next lazy
        # Provider session exists. The latest execution still names the old
        # session in that interval, so its context window alone is stale.
        with self.database.read() as connection:
            trace = agent_trace(connection, agent_id)
            if trace is None:
                return False
            reset = connection.execute(
                "SELECT 1 FROM sequence_segments member JOIN operation_positions boundary "
                "ON boundary.sequence_id=member.sequence_id AND boundary.ordinal>member.ordinal "
                "JOIN agent_operations operation USING(operation_id) "
                "WHERE member.sequence_id=%s AND member.segment_id=%s "
                "AND operation.kind IN ('context_reset','provider_context_reset') LIMIT 1",
                (trace["sequence_id"], source["segment_id"]),
            ).fetchone()
            if reset is not None:
                return False
        provider_session_ref = self.executions.latest_provider_session_ref(agent_id, since=since)
        if not provider_session_ref or source["provider_session_ref"] != provider_session_ref:
            return False
        return source["segment_id"] in self.session_context_ids(provider_session_ref, agent_id=agent_id)

    def session_context_head(self, provider_session_ref: str, *, agent_id: str = "") -> dict[str, Any] | None:
        """The compaction at the head of this Provider session, if any."""
        context = self.session_context_ids(provider_session_ref, agent_id=agent_id)
        members = self.segments.segment_members(context[:1])
        if members and members[0]["segment_type"] == "compaction":
            return self.segments.segment(members[0]["segment_id"])
        return None

    def session_context_ids(self, provider_session_ref: str, *, agent_id: str = "") -> list[str]:
        """A Provider session's current context as read from its Agent trace."""

        with self.database.read() as connection:
            return read_session_context_ids(
                connection, str(provider_session_ref or ""), agent_id=agent_id,
            )

    def session_compaction(
        self, provider_session_ref: str, provider_compaction_ref: str, *, agent_id: str = "",
    ) -> str:
        """The compaction recorded for a session under the Provider's own identity, if any.

        Recorded by one of the session's Executions, or (recorded by a
        backfill) held in the session's transcript.
        """

        session_ref = str(provider_session_ref or "")
        reference = str(provider_compaction_ref or "")
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT segment.segment_id FROM executions execution "
                "JOIN segments segment ON segment.execution_id = execution.execution_id "
                # segments_execution_idx covers only Segments of an Execution.
                "WHERE execution.provider_session_ref = %s AND segment.execution_id <> '' "
                "AND segment.segment_type = 'compaction' "
                "AND segment.metadata_json::jsonb ->> 'provider_compaction_ref' = %s LIMIT 1",
                (session_ref, reference),
            ).fetchone()
            if row is not None:
                return str(row["segment_id"])
            held = session_transcript_ids(connection, session_ref, agent_id=agent_id)
            return next(
                (
                    item["segment_id"] for item in read_segment_members(connection, held)
                    if item["segment_type"] == "compaction"
                    and item["metadata"].get("provider_compaction_ref") == reference
                ),
                "",
            )

    def record_context_received(
        self,
        agent_id: str,
        provider_session_ref: str,
        segment_ids: Sequence[str],
        *,
        actor_ref: str,
    ) -> str:
        """Record a context_received operation at the inherited trace members.

        The selected Segments must already belong to this Agent's trace.
        The operation records their delivery to the Provider session without
        inserting or reordering content. Repeated delivery is idempotent.
        """

        def write(connection: Any) -> str:
            return mark_context_received(
                connection, str(agent_id), str(provider_session_ref), segment_ids,
                actor_ref=actor_ref,
            )

        return self.database.write(write)

    def provider_context_snapshot(
        self,
        *,
        provider: str = "",
        backend_session_id: str = "",
        backend_route_id: str = "",
        provider_session_ref: str = "",
        through_turn_id: str = "",
        agent_id: str = "",
        context_cleared: bool = False,
    ) -> dict[str, Any] | None:
        """Freeze model context, not the audit transcript, in one read snapshot.

        A historical cutoff is resolved in what the session held, read from
        the Agent trace (``session_transcript_ids``), before applying its
        last compaction there. A later compaction must never leak future
        facts into a Fork-from-here snapshot.  An agent without a session is
        empty when it never ran, or when its context was cleared
        (``context_cleared``): the audit trace then holds only cleared work.
        """
        with self.database.snapshot() as connection:
            exact_session_ref = str(provider_session_ref or "").strip()
            if exact_session_ref:
                session = connection.execute(
                    'SELECT * FROM provider_sessions WHERE provider_session_ref=%s',
                    (exact_session_ref,),
                ).fetchone()
            else:
                session = connection.execute(
                    'SELECT * FROM provider_sessions WHERE provider=%s AND backend_route_id=%s AND backend_session_id=%s',
                    (provider, backend_route_id, backend_session_id),
                ).fetchone()
            context_ids = read_session_context_ids(
                connection, str(session["provider_session_ref"]), agent_id=agent_id,
            ) if session is not None and not through_turn_id else []
            if not through_turn_id and (session is None or not context_ids):
                if context_cleared or session is not None:
                    return {"segments": [], "empty_context": True}
                if agent_id and not (backend_session_id or provider_session_ref):
                    return read_agent_context(connection, agent_id)
                return None
            if session is None:
                return None
            if through_turn_id:
                # A historical cutoff reads what the session held from its
                # Agent trace, the one record of everything that happened.
                member_ids = session_transcript_ids(
                    connection, str(session["provider_session_ref"]), agent_id=agent_id,
                )
            else:
                member_ids = context_ids
            segments = read_segments(connection, member_ids)
            attach_provider_turns(connection, segments)
            if through_turn_id:
                positions = [i for i, item in enumerate(segments)
                             if item["provider_turn_id"] == through_turn_id]
                if not positions:
                    raise ValueError("Fork cutoff has no recorded Provider context")
                segments = segments[:positions[-1] + 1]
                compactions = [i for i, item in enumerate(segments)
                               if item["segment_type"] == "compaction"]
                if compactions:
                    segments = segments[compactions[-1]:]
            # What the Provider's own process put in its window stays with it.
            segments = [
                item for item in segments
                if (item.get("metadata") or {}).get("kind") not in PROVIDER_ONLY_KINDS
            ]
            return {
                "provider_session_ref": str(session["provider_session_ref"]),
                "provider": str(session["provider"]),
                "backend_session_id": str(session["backend_session_id"]),
                "backend_route_id": str(session["backend_route_id"]),
                "through_turn_id": through_turn_id,
                "segments": segments,
                "empty_context": not segments,
            }

    def fork_context_segments(self, references: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        ids = [str(item["segment_id"]) for item in references]
        with self.database.read() as connection:
            segments = read_segments(connection, ids)
            if [item["segment_id"] for item in segments] != ids:
                raise ValueError("Fork context snapshot is incomplete")
            for segment, reference in zip(segments, references):
                segment["provider_turn_id"] = str(reference.get("provider_turn_id") or "")
            return segments

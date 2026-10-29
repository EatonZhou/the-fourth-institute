"""Terminal facts a SocietyService stop dropped before a Resident's reply was routed.

The coordinator holds terminal facts in memory between the Conversation commit
and their consumption, so a stop in that gap loses them: the reply of a
Resident with Output route wires never starts its Trigger Path, and a Family
request stays at "Router 正在选择成员" (10-29: a publish restart came 2 s after
the Router finished). Once after each start, completed turns that a previous
process finalized but never routed are queued again as completed facts.
Consuming a fact twice delivers its Output once, so a fact the stopped process
did consume costs only a repeated check.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from management_common.database_time import instant, iso_time
from management_common.portal_database import _database_schema

# A dropped fact is seconds older than the stop that dropped it; the margin
# also covers a consumption retry that was still waiting when it stopped.
LOOKBACK = timedelta(minutes=30)


def _candidates_sql(agent_schema: str) -> str:
    agent = '"' + agent_schema + '"'
    return f"""
SELECT e.agent_id, e.execution_id, e.actor_ref, x.message_id
  FROM {agent}.executions e
  JOIN {agent}.execution_inputs x ON x.execution_id = e.execution_id AND x.role = 'primary'
 WHERE e.status = 'completed' AND COALESCE(e.final_message_id, '') <> ''
   AND e.finished_at >= %s AND e.finished_at < %s
   AND EXISTS (SELECT 1 FROM lineage_edges w JOIN lineages l ON l.lineage_id = w.lineage_id
                WHERE w.source_node_id = e.agent_id AND w.edge_type = 'route'
                  AND w.deleted_at IS NULL AND l.deleted_at IS NULL)
   -- A routed reply starts a path whose Event payload names its Execution.
   AND NOT EXISTS (SELECT 1 FROM trigger_paths p
                    WHERE (p.input_json::jsonb -> 'payload') @> jsonb_build_object('execution_id', e.execution_id))
   -- An open Program handoff is settled by handoff recovery instead.
   AND NOT EXISTS (SELECT 1 FROM trigger_path_steps s
                    WHERE s.kind = 'resident' AND s.node_id = e.agent_id AND s.input_message_id = x.message_id
                      AND s.status IN ('pending', 'running', 'waiting'))
 ORDER BY e.finished_at, e.execution_id"""


def _repository():
    from .resident_conversation_stream import get_repository

    return get_repository()


def find(started_at: str, *, lookback: timedelta = LOOKBACK) -> list[dict[str, Any]]:
    """Facts for completed turns finished before ``started_at`` whose reply started no Trigger Path."""
    from .runtime.contracts import ResidentTerminalFact
    from .state_store import get_lineage_data_store

    cutoff = instant(started_at)
    if cutoff is None:
        return []
    with get_lineage_data_store().database.read() as connection:
        rows = connection.execute(_candidates_sql(_database_schema("agent")), (cutoff - lookback, cutoff)).fetchall()
    repository = _repository()
    facts: list[dict[str, Any]] = []
    for row in rows:
        resident_id, message_id = str(row["agent_id"]), str(row["message_id"])
        try:
            state = repository.input_state(resident_id, message_id)
            # Only the stopped process finalized this input. A turn this
            # process finalizes reports its own fact.
            finished = instant(state.get("updated_at"))
            if str(state.get("state") or "") != "completed" or finished is None or finished >= cutoff:
                continue
            facts.append(ResidentTerminalFact(
                resident_id=resident_id, actor=str(row["actor_ref"] or ""), message_id=message_id,
                completed_at=iso_time(finished),
                business_ref=str(repository.message_business_ref(resident_id, message_id) or "")[:500],
            ).to_payload())
        except (KeyError, ValueError):
            continue
    return facts

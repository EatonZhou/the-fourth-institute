"""The replies Chat shows for its inputs, and each Execution's final reply."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .agent_data_chat import POSITION_METADATA, SHOWN_POSITION, ChatReads, chat_floors
from .agent_data_store import in_batches
from .conversation_projection import CHAT_PREDICATE


# The Segments carrying the given messages and those a group or Provider input
# made of them, ending in a SELECT over ``transforms`` (message_id, node).
_GROUPED_INPUTS = (
    "WITH RECURSIVE transforms(message_id,node) AS ("
    "SELECT metadata_json::jsonb#>>'{message,message_id}',segment_id FROM segments "
    f"WHERE {CHAT_PREDICATE} AND metadata_json::jsonb#>>'{{message,message_id}}'=ANY(%s) "
    "UNION SELECT transforms.message_id,relation.target_segment_id FROM transforms "
    "JOIN LATERAL (SELECT target_segment_id FROM segment_relations relation "
    "WHERE relation.source_segment_id=transforms.node "
    "AND relation.operation_type IN ('message.group','provider_input.compose','provider_input.append','agent_harness.prepare_invocation') "
    "OFFSET 0) relation ON TRUE) "
)


class ChatReplies:
    """Reads of the replies to Chat inputs."""

    def __init__(self, database: Any, *, chat: ChatReads) -> None:
        self.database = database
        self.chat = chat

    def chat_final_replies_many(
        self, requests: Mapping[str, Sequence[str]],
    ) -> dict[str, dict[str, tuple[str, str]]]:
        """For each input: (the final Agent answer, the last reply of any kind).

        Agent messages and Portal notices (failure, stop, compaction, command
        receipts) name the input they answer in ``reply_to_message_id``.  A
        Mailbox group's members are answered by their group.  An input that
        nothing answers is left out. Provider phases do not end a Portal input:
        after continuation its last current reply is the answer. The last reply
        of any kind supplies failure/stop notices.
        """

        wanted = {
            str(agent_id): list(dict.fromkeys(str(item or "").strip() for item in message_ids if str(item or "").strip()))
            for agent_id, message_ids in requests.items()
        }
        wanted = {agent_id: ids for agent_id, ids in wanted.items() if ids}
        if not wanted:
            return {}
        groups: dict[tuple[str, str], str] = {}
        last: dict[tuple[str, str], tuple[int, str]] = {}
        last_agent: dict[tuple[str, str], tuple[int, str]] = {}
        with self.database.read() as connection:
            traces = self.chat.chat_traces(list(wanted))
            sequences = list(traces.values())
            if not sequences:
                return {}
            asked = list(dict.fromkeys(item for agent_id in traces for item in wanted[agent_id]))
            for chunk in in_batches(asked):
                for row in connection.execute(
                    _GROUPED_INPUTS
                    # Each node's few memberships first: the planner overestimates the
                    # transforms and would otherwise sort every position of the traces.
                    + "SELECT position.sequence_id,transforms.message_id,position.message_id AS group_id "
                    "FROM transforms JOIN LATERAL (SELECT sequence_id FROM sequence_segments member "
                    "WHERE member.segment_id=transforms.node AND member.sequence_id=ANY(%s) OFFSET 0) member ON TRUE "
                    "JOIN LATERAL (SELECT position.sequence_id,position.message_id FROM chat_message_positions position "
                    "WHERE position.sequence_id=member.sequence_id AND position.source_kind='segment' "
                    f"AND position.source_id=transforms.node AND {SHOWN_POSITION} OFFSET 0) position ON TRUE "
                    # A notice is never transformed; it stands for itself.
                    "UNION ALL SELECT position.sequence_id,position.message_id,position.message_id "
                    "FROM chat_message_positions position WHERE position.sequence_id=ANY(%s) "
                    "AND position.source_kind='notice' AND position.message_id=ANY(%s)",
                    (list(chunk), sequences, sequences, list(chunk)),
                ).fetchall():
                    if row["group_id"]:
                        groups[(str(row["sequence_id"]), str(row["message_id"]))] = str(row["group_id"])
            # An input is answered in its own trace: look each one up there, not
            # in every requested trace (fifty Residents made 5000 lookups).
            pairs = list(dict.fromkeys(
                (sequence_id, identity)
                for agent_id, sequence_id in traces.items()
                for message_id in wanted[agent_id]
                for identity in (message_id, groups.get((sequence_id, message_id), ""))
                if identity
            ))
            rows = []
            for chunk in in_batches(pairs):
                rows.extend(connection.execute(
                    "SELECT requested.sequence_id,reply.* "
                    "FROM unnest(%s::text[],%s::text[]) requested(sequence_id,reply_to) "
                    "CROSS JOIN (VALUES ('model'),('harness')) kind(segment_type) "
                    "CROSS JOIN LATERAL (SELECT position.ordinal,position.segment_type,"
                    "position.reply_to,position.message_id FROM chat_message_positions position "
                    "WHERE position.sequence_id=requested.sequence_id AND position.reply_to IS NOT NULL "
                    "AND position.reply_to=requested.reply_to AND position.source_kind<>'sync' AND position.reply_candidate "
                    f"AND position.segment_type=kind.segment_type AND {SHOWN_POSITION} "
                    "ORDER BY position.ordinal DESC LIMIT 1) reply",
                    ([sequence_id for sequence_id, _identity in chunk], [identity for _sequence_id, identity in chunk]),
                ).fetchall())
            for row in rows:
                key = (str(row["sequence_id"]), str(row["reply_to"]))
                found = (int(row["ordinal"]), str(row["message_id"]))
                for held in (last, last_agent) if row["segment_type"] == "model" else (last,):
                    if key not in held or found[0] > held[key][0]:
                        held[key] = found
        replies: dict[str, dict[str, tuple[str, str]]] = {}
        for agent_id, sequence_id in traces.items():
            for message_id in wanted[agent_id]:
                key = (sequence_id, message_id)
                if key not in last:
                    key = (sequence_id, groups.get(key, ""))
                if key in last:
                    answer = last_agent.get(key, (0, ""))[1]
                    replies.setdefault(agent_id, {})[message_id] = (answer, last[key][1])
        return replies

    def chat_replies(
        self,
        agent_id: str,
        message_id: str,
        *,
        until_message_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """The shown Agent messages that reply to ``message_id``, oldest first: the last ``limit``.

        Replies come after the message they answer; ``until_message_id`` (its
        final reply) ends the search, so a finished turn reads only its own
        messages.  Without it the search runs to the end of the Conversation.
        """

        return self.chat_replies_many(
            {agent_id: {message_id: until_message_id}}, limit=limit,
        ).get(agent_id, {}).get(message_id, [])

    def chat_replies_many(
        self, requests: Mapping[str, Mapping[str, str]], *, limit: int = 20,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Read bounded reply windows together; each input keeps its own limit and order.

        Requests map Agent and input identity to an optional final reply identity.
        Positions and payloads are read in batches, never one round trip per input.
        """
        wanted = {agent: dict(inputs) for agent, inputs in requests.items() if inputs}
        result = {agent: {message: [] for message in inputs} for agent, inputs in wanted.items()}
        if not wanted:
            return result
        with self.database.read() as connection:
            traces = self.chat.chat_traces(list(wanted))
            if not traces:
                return result
            floors = chat_floors(connection, traces.values())
            identities = list(dict.fromkeys(
                identity for agent in traces for message, until in wanted[agent].items()
                for identity in (message, until) if identity
            ))
            positions: dict[tuple[str, str], int] = {}
            for chunk in in_batches(identities):
                rows = connection.execute(
                    "SELECT position.sequence_id, MAX(position.ordinal) AS ordinal, position.message_id "
                    "FROM chat_message_positions position WHERE position.message_id = ANY(%s) "
                    "AND position.sequence_id = ANY(%s) AND position.source_kind <> 'sync' "
                    f"AND {SHOWN_POSITION} GROUP BY position.sequence_id, position.message_id",
                    (list(chunk), list(traces.values())),
                ).fetchall()
                for row in rows:
                    key = (str(row["sequence_id"]), str(row["message_id"]))
                    positions[key] = max(positions.get(key, 0), int(row["ordinal"]))
            windows = [
                (agent, message, sequence, max(positions[(sequence, message)], floors.get(sequence, 0)),
                 positions.get((sequence, until), 2147483647))
                for agent, sequence in traces.items() for message, until in wanted[agent].items()
                if (sequence, message) in positions
            ]
            for chunk in in_batches(windows):
                # Each input's newest replies by chat_message_positions_reply_idx.
                rows = connection.execute(
                    f"""
                    SELECT requested.agent_id,requested.message_id,reply.*
                    FROM unnest(%s::text[],%s::text[],%s::text[],%s::integer[],%s::integer[])
                         requested(agent_id,message_id,sequence_id,after_ordinal,until_ordinal)
                    CROSS JOIN LATERAL (
                        SELECT position.ordinal,position.source_id AS segment_id,
                               {POSITION_METADATA} AS metadata_json
                        FROM chat_message_positions position
                        WHERE position.sequence_id=requested.sequence_id AND position.reply_to=requested.message_id
                          AND position.segment_type='model'
                          AND (position.reply_candidate OR EXISTS (
                              SELECT 1 FROM jsonb_array_elements(
                                  COALESCE(({POSITION_METADATA})#>'{{message,parts}}', '[]'::jsonb)
                              ) part WHERE part->>'type' IN ('tool_call','tool_result')
                                AND part->>'tool_name' IN ('request_user_input','request_approval','mcp_elicitation')
                          ))
                          AND position.reply_to IS NOT NULL AND position.source_kind<>'sync'
                          AND position.ordinal > requested.after_ordinal AND position.ordinal <= requested.until_ordinal
                          AND {SHOWN_POSITION}
                        ORDER BY position.ordinal DESC LIMIT %s
                    ) reply
                    ORDER BY requested.agent_id,requested.message_id,reply.ordinal
                    """,
                    (*[list(column) for column in zip(*chunk)], max(1, int(limit))),
                ).fetchall()
                unique = {row["segment_id"]: row for row in rows}
                messages = {message["segment_id"]: message for message in self.chat.messages_from_rows(list(unique.values()))}
                for row in rows:
                    message = messages[row["segment_id"]]
                    if message["role"] == "agent":
                        result[row["agent_id"]][row["message_id"]].append(dict(message))
        return result

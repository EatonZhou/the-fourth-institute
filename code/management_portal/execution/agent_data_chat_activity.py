"""Chat activity: who wrote last, unread counts, conversation summaries and headers."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from management_common.database_time import instant, iso_time

from .agent_data_chat import POSITION_METADATA, SHOWN_POSITION, ChatReads, chat_floors, chat_payload
from .agent_data_store import canonical_json, finish_conversation_summary, in_batches
from .agent_data_traces import trace_ids


# Whether the message has a body: its Segment's payload, or the notice's text.
_POSITION_HAS_CONTENT = (
    "CASE WHEN position.source_kind='segment' THEN COALESCE((SELECT payload.byte_length "
    "FROM segments segment JOIN payloads payload ON payload.payload_ref=segment.content_ref "
    "WHERE segment.segment_id=position.source_id), 0) > 0 "
    "ELSE COALESCE((SELECT octet_length(convert_to(data_json::jsonb->>'content','UTF8')) "
    "FROM agent_operations WHERE operation_id=position.source_id), 0) > 0 END"
)


def _user_message_rows(connection, sequences, floors, *, excluded_business_ref="", actor_spellings=()):
    """Each trace's user-message count, newest message and the actor's requests after its last clear."""
    excluded, parameters = "", ()
    if excluded_business_ref:
        excluded = " AND NOT (position.business_ref IS NOT NULL AND position.business_ref ~ %s)"
        parameters = (excluded_business_ref,)
    return list(connection.execute(
        "SELECT position.sequence_id, COUNT(*) AS count, MAX(position.created_at) AS last_at, "
        "(ARRAY_AGG(position.actor_ref ORDER BY position.created_at DESC, position.ordinal DESC))[1] "
        "AS last_actor, ARRAY_AGG(ARRAY[position.created_at::text, position.message_id] "
        "ORDER BY position.created_at, position.ordinal) "
        "FILTER (WHERE lower(position.actor_ref) = ANY(%s)) AS actor_requests "
        "FROM chat_message_positions position WHERE position.sequence_id = ANY(%s) "
        "AND position.segment_type = 'input' AND position.source_kind <> 'sync' "
        "AND position.ordinal > COALESCE((%s::jsonb ->> position.sequence_id)::int, 0)"
        f"{excluded} GROUP BY position.sequence_id",
        (list(actor_spellings), list(sequences),
         canonical_json({key: floors[key] for key in sequences if key in floors}), *parameters),
    ).fetchall())


def _addressed_rows(connection, sequences, floor_json: str, actor: str) -> list[Any]:
    """Messages naming the actor as a recipient, oldest first per trace."""
    return list(connection.execute(
        "SELECT position.sequence_id, position.created_at, position.message_id "
        "FROM chat_message_positions position WHERE position.sequence_id = ANY(%s) "
        "AND position.segment_type = 'model' AND position.source_kind <> 'sync' "
        "AND position.recipient_users @> ARRAY[lower(%s)] "
        "AND position.ordinal > COALESCE((%s::jsonb ->> position.sequence_id)::int, 0) "
        "ORDER BY position.sequence_id, position.created_at, position.ordinal",
        (list(sequences), actor, floor_json),
    ).fetchall())


def activity_rows(
    connection: Any,
    actor: str,
    sequence_ids: Sequence[str] | None,
    *,
    after: tuple[str, str] | None = None,
    limit: int | None = None,
    exclude_agent_ids: Sequence[str] = (),
    known_times: Mapping[str, Any] | None = None,
) -> list[Any]:
    """Each trace's latest Chat activity with the actor: user messages it sent, messages to it.

    Read from the activity kept for each trace and person, already counted
    from the trace's last context reset: no grouping of all their messages
   .  The traces' projections must be current.
    """
    scope = " AND activity.sequence_id = ANY(%s)" if sequence_ids is not None else ""
    exclusions = " AND agent.agent_id <> ALL(%s)" if exclude_agent_ids else ""
    # Times the caller knows beyond the messages raise a listed agent's
    # activity, ordering and bounding pages in SQL; they list no agent.
    known = (" LEFT JOIN unnest(%s::text[], %s::timestamptz[]) AS known(agent_id, at)"
             " ON known.agent_id = agent.agent_id") if known_times else ""
    latest = "GREATEST(MAX(activity.latest_at), MAX(known.at))" if known_times else "MAX(activity.latest_at)"
    boundary = (
        f" HAVING {latest} < %s OR ({latest} = %s AND agent.agent_id > %s)"
    ) if after else ""
    paging = " ORDER BY latest_at DESC, agent.agent_id ASC LIMIT %s" if limit is not None else ""
    return list(connection.execute(
        f"SELECT agent.agent_id, {latest} AS latest_at FROM chat_actor_activity activity "
        "JOIN agents agent ON agent.trace_sequence_id = activity.sequence_id" + known
        + " WHERE activity.actor = lower(%s)" + scope + exclusions
        + " GROUP BY agent.agent_id" + boundary + paging,
        [*([list(known_times), list(known_times.values())] if known_times else []),
         actor, *([list(sequence_ids)] if sequence_ids is not None else []),
         *([list(exclude_agent_ids)] if exclude_agent_ids else []),
         *([after[0], after[0], after[1]] if after else []),
         *([limit] if limit is not None else [])],
    ).fetchall())


class ChatActivity:
    """Summaries of Chat activity across many Agents."""

    def __init__(self, database: Any, *, chat: ChatReads) -> None:
        self.database = database
        self.chat = chat
        self._conversation_summaries_lock = threading.Lock()
        self._conversation_summaries_cache: OrderedDict[
            tuple[str, str, str], tuple[tuple[str, str], float, dict[str, Any]]
        ] = OrderedDict()

    def chat_user_counts(self, agent_ids: Sequence[str]) -> dict[str, int]:
        """How many user messages each Agent's current Chat holds."""
        return {agent: facts["count"] for agent, facts in self.user_message_summaries(agent_ids).items()}

    def chat_actor_activity_page(
        self, actor_ref: str, *, after: tuple[str, str] | None = None,
        limit: int = 50, exclude_agent_ids: Sequence[str] = (),
        known_times: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, str]]:
        """Return one ordered activity page; grouping and keyset bounds stay in SQL.

        ``known_times`` maps agents to later times the caller knows of, such
        as outcomes; an agent's activity is the latest of them and its messages.
        A listing brings every trace current once (``bring_chat_current``)
        before it reads its pages.
        """
        actor = str(actor_ref or "").strip()
        if not actor:
            return []
        with self.database.read() as connection:
            rows = activity_rows(
                connection, actor, None, after=after, limit=limit,
                exclude_agent_ids=exclude_agent_ids, known_times=known_times,
            )
        return [(str(row["agent_id"]), (iso_time(row["latest_at"]) or "")) for row in rows]

    def chat_actor_activity(
        self, actor_ref: str, agent_ids: Iterable[str] | None = None,
    ) -> dict[str, str]:
        """Latest activity of each agent with the actor: user messages it sent there, or messages to it.

        The copies a Family facade relays to its members do not count: they
        keep their sender, but the sender's conversation is with the facade
        (chat_position_index.RELAYED_COPY_PATTERN, decision_44cf587c1721).  A message a Resident
        sends to the actor counts where it is written: in the sender's
        Conversation, or a Family facade's (decision_c524c775e0be).  Newest first.
        """
        actor = str(actor_ref or "").strip()
        requested = None if agent_ids is None else tuple(dict.fromkeys(
            str(item or "").strip() for item in agent_ids if str(item or "").strip()
        ))
        if not actor or requested == ():
            return {}
        rows: list[Any] = []
        if requested is None:
            self.chat.bring_chat_current()
            with self.database.read() as connection:
                rows = activity_rows(connection, actor, None)
        else:
            with self.database.read() as connection:
                traces = self.chat.chat_traces(list(requested))
                for chunk in in_batches(list(traces.values())):
                    rows.extend(activity_rows(connection, actor, list(chunk)))
        ordered = sorted(((str(row["agent_id"]), (iso_time(row["latest_at"]) or "")) for row in rows), key=lambda item: item[0])
        ordered.sort(key=lambda item: item[1], reverse=True)
        return dict(ordered)

    @staticmethod
    def chat_activity_page_token(agent_id: str, latest_at: str) -> str:
        return base64.urlsafe_b64encode(json.dumps(
            [1, latest_at, agent_id], separators=(",", ":"),
        ).encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def chat_activity_page_boundary(page_token: str) -> tuple[str, str] | None:
        """The (latest_at, agent_id) a page token continues after; None for the first page."""

        if not page_token:
            return None
        try:
            if len(page_token) > 2048:
                raise ValueError("oversized cursor")
            boundary = json.loads(base64.b64decode(
                page_token + "=" * (-len(page_token) % 4),
                altchars=b"-_", validate=True,
            ))
            if (
                not isinstance(boundary, list) or len(boundary) != 3
                or boundary[0] != 1
                or not all(isinstance(item, str) and item for item in boundary[1:])
            ):
                raise ValueError("invalid cursor")
            return (boundary[1], boundary[2])
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid conversation page token") from exc

    def chat_signatures(self, agent_ids: Sequence[str]) -> dict[str, str]:
        """A value that changes whenever an Agent's trace, and so its Chat, changes."""

        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        if not requested:
            return {}
        facts: dict[str, list[object]] = {}
        with self.database.read() as connection:
            for chunk in in_batches(requested):
                # The length is the trace's last position, a Segment's or a notice's.
                for row in connection.execute(
                    "SELECT agent.agent_id, sequence.sequence_id, sequence.updated_at, GREATEST("
                    "(SELECT MAX(member.ordinal) FROM sequence_segments member "
                    " WHERE member.sequence_id = sequence.sequence_id), "
                    "(SELECT MAX(position.ordinal) FROM operation_positions position JOIN LATERAL ("
                    " SELECT 1 FROM agent_operations operation WHERE operation.operation_id=position.operation_id "
                    " AND operation.kind='notice' OFFSET 0) notice ON TRUE "
                    " WHERE position.sequence_id = sequence.sequence_id)) AS length "
                    "FROM agents agent JOIN sequences sequence ON sequence.sequence_id = agent.trace_sequence_id "
                    "WHERE agent.agent_id = ANY(%s)",
                    (list(chunk),),
                ).fetchall():
                    if row["length"]:
                        facts[str(row["agent_id"])] = [
                            str(row["sequence_id"]), int(row["length"]), (iso_time(row["updated_at"]) or ""),
                        ]
        return {
            agent_id: hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
            for agent_id, value in facts.items()
        }

    def user_message_summaries(
        self, agent_ids: Sequence[str], *, excluded_business_ref: str = "",
    ) -> dict[str, dict[str, Any]]:
        """Count, newest time and sender of each Agent's user messages, in one pass over its trace."""

        summaries = {agent_id: {"count": 0, "last_at": "", "last_actor": ""} for agent_id in agent_ids}
        with self.database.read() as connection:
            traces = self.chat.chat_traces(list(agent_ids))
            owners = {sequence_id: agent_id for agent_id, sequence_id in traces.items()}
            floors = chat_floors(connection, traces.values())
            for chunk in in_batches(list(traces.values())):
                for row in _user_message_rows(
                    connection, chunk, floors, excluded_business_ref=excluded_business_ref,
                ):
                    summaries[owners[str(row["sequence_id"])]] = {
                        "count": int(row["count"]), "last_at": (iso_time(row["last_at"]) or ""),
                        "last_actor": str(row["last_actor"]),
                    }
        return summaries

    def chat_conversation_summaries(
        self, agent_ids: Sequence[str], actor_ref: str, *, excluded_business_ref: str = "",
        finish: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """User-message facts of each conversation, the actor's requests in it and the latest message to the actor.

        Messages whose ``business_ref`` matches ``excluded_business_ref`` (a
        regular expression) do not count, such as the copies a Family facade
        relays to its members: the sender's conversation is with the facade.
        With ``finish=False`` the actor's requests stay as ``actor_requests``
        (time, message id) pairs, for a caller that adds the messages waiting
        in a Mailbox before ``finish_conversation_summary``.
        """
        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        if not requested:
            return {}
        actor = str(actor_ref or "").strip().lstrip("@＠").strip()
        summaries = {agent_id: {
            "user_message_count": 0, "last_user_actor": "", "last_user_message_at": "",
            "actor_requests": [], "actor_addressed_message_id": "", "actor_addressed_at": "",
        } for agent_id in requested}
        actor_spellings = [prefix + actor.casefold() for prefix in ("", "@", "＠")] if actor else []
        # Counts and this actor's requests describe the same user messages.
        # Read them in one pass, with trace/reset/recipient facts from the same
        # committed snapshot, instead of scanning every Chat twice.
        self.chat.chat_traces(requested)
        with self.database.snapshot() as connection:
            traces = trace_ids(connection, requested)
            owners = {sequence_id: agent_id for agent_id, sequence_id in traces.items()}
            floors = chat_floors(connection, traces.values())
            # Sequence rows change for every append, replacement, notice and
            # reset. Their MVCC identity also distinguishes equal timestamps
            # and replacements which keep the same trace length. Read it in
            # the same committed snapshot as the facts it guards.
            versions = {}
            if connection.execute("SHOW transaction_read_only").fetchone()[0] == "on":
                versions = {
                    str(row["sequence_id"]): (str(row["version"]), str(row["position"]))
                    for row in connection.execute(
                        "SELECT sequence_id, xmin::text AS version, ctid::text AS position "
                        "FROM sequences WHERE sequence_id = ANY(%s)",
                        (list(traces.values()),),
                    ).fetchall()
                }
            pending = []
            for sequence_id in traces.values():
                key = (actor.casefold(), excluded_business_ref, sequence_id)
                with self._conversation_summaries_lock:
                    cached = self._conversation_summaries_cache.get(key)
                    if cached and cached[0] == versions.get(sequence_id) and cached[1] > time.monotonic():
                        self._conversation_summaries_cache.move_to_end(key)
                    else:
                        cached = None
                if cached is None:
                    pending.append(sequence_id)
                else:
                    summaries[owners[sequence_id]] = deepcopy(cached[2])
            for chunk in in_batches(pending):
                floor_json = canonical_json({key: floors[key] for key in chunk if key in floors})
                for row in _user_message_rows(
                    connection, chunk, floors, excluded_business_ref=excluded_business_ref, actor_spellings=actor_spellings,
                ):
                    summaries[owners[str(row["sequence_id"])]].update({
                        "user_message_count": int(row["count"]),
                        "last_user_actor": str(row["last_actor"]),
                        "last_user_message_at": (iso_time(row["last_at"]) or ""),
                        "actor_requests": [(iso_time(instant(at)), str(message_id))
                                           for at, message_id in row["actor_requests"] or []],
                    })
                if not actor:
                    continue
                for row in _addressed_rows(connection, chunk, floor_json, actor):
                    summary = summaries[owners[str(row["sequence_id"])]]
                    summary["actor_addressed_message_id"] = str(row["message_id"])
                    summary["actor_addressed_at"] = (iso_time(row["created_at"]) or "")
            for sequence_id in pending:
                summary = summaries[owners[sequence_id]]
                if sequence_id not in versions or len(summary["actor_requests"]) > 4096:
                    continue
                key = (actor.casefold(), excluded_business_ref, sequence_id)
                value = (versions[sequence_id], time.monotonic() + 60.0, deepcopy(summary))
                with self._conversation_summaries_lock:
                    self._conversation_summaries_cache[key] = value
                    self._conversation_summaries_cache.move_to_end(key)
                    while (
                        len(self._conversation_summaries_cache) > 2048
                        or sum(len(item[2]["actor_requests"]) for item in self._conversation_summaries_cache.values()) > 65536
                    ):
                        self._conversation_summaries_cache.popitem(last=False)
        if finish:
            for summary in summaries.values():
                finish_conversation_summary(summary)
        return summaries

    def chat_message_headers(
        self,
        agent_ids: Sequence[str],
        *,
        message_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """User messages and replies of many Agents' Chats, without their bodies."""

        requested = list(dict.fromkeys(str(item) for item in agent_ids if str(item)))
        if not requested:
            return []
        selected_ids = (
            list(dict.fromkeys(str(item) for item in message_ids if str(item)))
            if message_ids is not None else None
        )
        if selected_ids == []:
            return []
        rows: list[dict[str, Any]] = []
        with self.database.read() as connection:
            traces = self.chat.chat_traces(requested)
            owners = {sequence_id: agent_id for agent_id, sequence_id in traces.items()}
            floors = chat_floors(connection, traces.values())
            for chunk in in_batches(list(traces.values())):
                floor_json = canonical_json({key: floors[key] for key in chunk if key in floors})
                # User messages and the replies naming the input they answer,
                # after each trace's last clear.
                clauses = (
                    "position.sequence_id = ANY(%s) AND position.source_kind <> 'sync' "
                    "AND position.ordinal > COALESCE((%s::jsonb ->> position.sequence_id)::int, 0) "
                    "AND (position.segment_type = 'input' OR (position.segment_type = 'model' "
                    f"AND position.reply_to IS NOT NULL)) AND position.reply_candidate AND {SHOWN_POSITION}"
                )
                parameters: tuple[Any, ...] = (list(chunk), floor_json)
                if selected_ids is not None:
                    # The named messages by their identity (chat_message_positions_identity_idx).
                    clauses = "position.message_id = ANY(%s) AND " + clauses
                    parameters = (selected_ids, *parameters)
                query = (
                    f"SELECT position.sequence_id, position.source_id AS segment_id, {POSITION_METADATA} AS metadata_json, "
                    f"{_POSITION_HAS_CONTENT} AS has_content FROM (SELECT * FROM chat_message_positions position "
                    f"WHERE {clauses} OFFSET 0) position"
                )
                for row in connection.execute(query, parameters).fetchall():
                    message = chat_payload(row["segment_id"], row["metadata_json"], "")
                    reply_to = str(message.get("reply_to_message_id") or "")
                    if message["role"] != "user" and not (message["role"] == "agent" and reply_to):
                        continue
                    rows.append({
                        "agent_id": owners[str(row["sequence_id"])],
                        "message_id": str(message["message_id"]),
                        "role": str(message["role"]),
                        "actor_ref": str(message.get("author_id") or ""),
                        "accepted_at": str(message.get("created_at") or ""),
                        "display_state": "displayed",
                        "reply_to_message_id": reply_to,
                        "author_id": str(message.get("author_id") or ""),
                        "has_content": bool(row["has_content"]),
                    })
        rows.sort(key=lambda item: (item["agent_id"], item["accepted_at"], item["message_id"]))
        return rows

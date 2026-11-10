"""The questions waiting for a User's answer, across every Resident Conversation.

An Agent message asks a User something in two ways: a question set (its
``portal-question`` fences, answered with one message) or a native tool
request (``request_user_input``, ``request_approval``, ``mcp_elicitation``),
answered while its turn waits.  ``user_pending_requests`` keeps one row per
User and asking message, written in the same transaction as the message, so a
read does not depend on how far back the message lies in its Conversation.

Who is asked: each User the message names as recipient, and the User who
wrote the input the message answers (every member of a Mailbox group).  An
input a Resident wrote asks no User, and nothing is inferred from question
marks or from who may manage the Resident.  A relayed input keeps its
sender; the User then answers in the outermost Family facade that holds the
relay, so the row opens there while its key stays the member's message.

The index is complete or the read fails.  A message whose rows could not be
worked out leaves a repair row in the same transaction; the next read redoes
it first and fails rather than answer without it.  A store that had
Conversations before the index existed is not ready until ``backfill_step``
(SocietyService housekeeping) has indexed them once (``READY_KEY`` in
``lineage_meta``).

The row only says where to look.  A read checks each row against its holder's
current state (``question_set_state``, the native request's current status),
the facade's current relay, and the User's current right to read and answer
there; a row whose question no longer waits is removed.  Reading a question
does not answer it.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_common.async_questions import decode_question_content, question_set
from management_common.principal import actor_principal, message_recipients
from management_common.provider_interactions import INTERACTIVE_TOOL_NAMES


LOGGER = logging.getLogger(__name__)

TABLE = "user_pending_requests"
REPAIR_TABLE = "user_pending_request_repairs"
READY_KEY = "user_pending_requests"
# Created when absent; an existing table is not locked on process start.
SCHEMA_SQL = """
DO $user_pending_requests$
BEGIN
    IF to_regclass('user_pending_requests') IS NULL THEN
        CREATE TABLE user_pending_requests (
            user_id TEXT NOT NULL,
            source_resident_id TEXT NOT NULL,
            source_message_id TEXT NOT NULL,
            resident_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (user_id, source_resident_id, source_message_id)
        );
        CREATE INDEX user_pending_requests_source_idx
            ON user_pending_requests(source_resident_id, source_message_id);
        -- A store without Conversation history has nothing to backfill.
        IF NOT EXISTS (SELECT 1 FROM conversation_events LIMIT 1) THEN
            INSERT INTO lineage_meta(key, value) VALUES ('user_pending_requests', 'ready')
                ON CONFLICT (key) DO UPDATE SET value='ready';
        END IF;
    END IF;
    IF to_regclass('user_pending_request_repairs') IS NULL THEN
        CREATE TABLE user_pending_request_repairs (
            resident_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            PRIMARY KEY (resident_id, message_id)
        );
    END IF;
END
$user_pending_requests$
"""
COLUMNS = "user_id, source_resident_id, source_message_id, resident_id, message_id, kind, created_at"


class IndexNotReady(RuntimeError):
    """The index cannot say every waiting question now; the caller must not show an empty list."""


def create_schema(connection: Any) -> None:
    """Create the index when absent; created in a store without history, it is ready at once."""

    connection.execute(SCHEMA_SQL)


def mark_ready(connection: Any) -> None:
    connection.execute(
        "INSERT INTO lineage_meta(key, value) VALUES (%s, 'ready') ON CONFLICT (key) DO UPDATE SET value='ready'",
        (READY_KEY,),
    )


def _user_key(user_id: object) -> str:
    return canonical_actor_id(user_id).casefold()


def request_key(source_resident_id: str, source_message_id: str) -> str:
    return f"{source_resident_id}/message/{source_message_id}"


def _native_kind(part: Mapping[str, Any]) -> str:
    return "approval" if part.get("tool_name") == "request_approval" else "question"


def asked_kind(resident_id: str, message: Mapping[str, Any]) -> str:
    """What an Agent message asks: a native request kind, ``question_set``, or "" for nothing."""

    if message.get("role") != "agent":
        return ""
    parts = [dict(part) for part in message.get("content_parts") or [] if isinstance(part, Mapping)]
    for part in parts:
        if part.get("type") == "tool_call" and part.get("tool_name") in INTERACTIVE_TOOL_NAMES:
            return _native_kind(part)
    decoded = {**message, "content_parts": decode_question_content(parts, resident_id, str(message.get("message_id") or ""))}
    return "question_set" if question_set(decoded) is not None else ""


def _answered_inputs(resident_id: str, message: Mapping[str, Any]) -> list[str]:
    from . import resident_mailbox

    reply_to = str(message.get("reply_to_message_id") or "").strip()
    if not reply_to:
        return []
    return resident_mailbox.members_of({"resident_id": resident_id}, reply_to) or [reply_to]


def _open_place(resident_id: str, input_id: str, message_id: str, author: str, repository: Any) -> str:
    """Where the input's author sees the question: the outermost facade holding the relay they wrote.

    A marked relay is followed edge by edge; an unmarked (historical) one is
    asked of each Family facade around the Resident, as its projection does.
    """

    from . import family_facade, resident_record_domain

    place = resident_id
    sources = family_facade.relay_sources_of(resident_id, input_id)
    for facade_id, facade_message_id in sources:
        relayed = repository.message(facade_id, facade_message_id) or {}
        if _user_key(relayed.get("author_id")) == author:
            place = facade_id
    if sources:
        return place
    for facade_id in family_facade.identities_of(resident_id)[1:]:
        facade = resident_record_domain.find_resident(facade_id)
        found = family_facade.relayed_reply(facade, message_id) if family_facade.is_facade(facade) else None
        if found is None or found["worker_id"] != resident_id:
            continue
        relayed = repository.message(facade_id, found["request_message_id"]) or {}
        if _user_key(relayed.get("author_id")) == author:
            place = facade_id
    return place


def asked_users(resident_id: str, message: Mapping[str, Any], repository: Any) -> dict[str, str]:
    """``{user: resident to open}`` for each User an Agent message asks."""

    asked: dict[str, str] = {}
    for recipient in message_recipients(message):
        if recipient.get("kind") == "user" and recipient.get("id"):
            asked.setdefault(_user_key(recipient["id"]), resident_id)
    message_id = str(message.get("message_id") or "")
    for input_id in _answered_inputs(resident_id, message):
        stored = repository.message(resident_id, input_id) or {}
        author = canonical_actor_id(stored.get("author_id"))
        if stored.get("role") != "user" or not author or actor_principal(author).kind != "user":
            continue
        key = _user_key(author)
        asked.setdefault(key, _open_place(resident_id, input_id, message_id, key, repository))
    return asked


def rows_for(resident_id: str, message: Mapping[str, Any], repository: Any) -> list[tuple[Any, ...]]:
    """The index rows of one message; raises when they cannot be worked out."""

    kind = asked_kind(resident_id, message)
    if not kind:
        return []
    message_id = str(message.get("message_id") or "")
    created_at = str(message.get("created_at") or "") or None
    return [
        (user, resident_id, message_id, place, message_id, kind, created_at)
        for user, place in asked_users(resident_id, message, repository).items()
    ]


class Pending:
    """What one message write adds to the index: its rows, or a repair when they could not be worked out."""

    def __init__(self, resident_id: str, message_id: str, rows: list[tuple[Any, ...]] | None) -> None:
        self.resident_id, self.message_id, self.rows = resident_id, message_id, rows

    def write(self, connection: Any) -> None:
        if self.rows is None:
            connection.execute(
                f"INSERT INTO {REPAIR_TABLE}(resident_id, message_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (self.resident_id, self.message_id),
            )
            return
        record(connection, self.rows)


def prepare(resident_id: str, message: Mapping[str, Any], repository: Any) -> Pending:
    """The index change of an Agent message, worked out before its write transaction.

    The message is delivered whatever happens here; a failure leaves a repair
    row instead of rows, which the next read redoes.
    """

    message_id = str(message.get("message_id") or "")
    try:
        return Pending(resident_id, message_id, rows_for(resident_id, message, repository))
    except Exception:  # noqa: BLE001 - delivery must not depend on this index; the repair row recovers it
        LOGGER.exception("could not index the questions of %s/%s; left for repair", resident_id, message_id)
        return Pending(resident_id, message_id, None)


def record(connection: Any, rows: Iterable[tuple[Any, ...]]) -> None:
    for row in rows:
        connection.execute(
            f"INSERT INTO {TABLE} ({COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz, now())) "
            "ON CONFLICT (user_id, source_resident_id, source_message_id) "
            "DO UPDATE SET kind=EXCLUDED.kind, resident_id=EXCLUDED.resident_id",
            row,
        )


def reindex(resident_id: str, message_id: str, repository: Any) -> list[tuple[Any, ...]]:
    """The rows of one stored message as it is now; [] when it is gone or asks nothing."""

    message = repository.message(resident_id, message_id)
    return rows_for(resident_id, message, repository) if message is not None else []


def _repair(store: Any, repository: Any) -> None:
    with store.database.read() as connection:
        ready = connection.execute("SELECT value FROM lineage_meta WHERE key=%s", (READY_KEY,)).fetchone()
        repairs = connection.execute(f"SELECT resident_id, message_id FROM {REPAIR_TABLE}").fetchall()
    if not ready or ready["value"] != "ready":
        LOGGER.warning("pending-question index is still being backfilled by SocietyService housekeeping")
        raise IndexNotReady("待答问题索引暂不可用")
    for item in repairs:
        try:
            rows = reindex(item["resident_id"], item["message_id"], repository)
        except Exception as exc:  # noqa: BLE001 - reported to the caller instead of a partial list
            # The object stays in the server log: the caller may not see it.
            LOGGER.exception("pending-question repair failed for %s/%s", item["resident_id"], item["message_id"])
            raise IndexNotReady("待答问题索引暂不可用") from exc

        def write(connection: Any, rows: list[tuple[Any, ...]] = rows, item: Mapping[str, Any] = item) -> None:
            record(connection, rows)
            connection.execute(f"DELETE FROM {REPAIR_TABLE} WHERE resident_id=%s AND message_id=%s",
                               (item["resident_id"], item["message_id"]))

        store.database.write(write)


def _native_waiting(holder: str, message: Mapping[str, Any], repository: Any) -> bool:
    current = repository.tool_status_messages([dict(message)], resident_id=holder)[0]
    return any(
        part.get("type") == "tool_call" and part.get("tool_name") in INTERACTIVE_TOOL_NAMES
        and str(part.get("status") or "pending") == "pending"
        for part in current.get("content_parts") or []
    )


def _waiting(row: Mapping[str, Any], place_record: Mapping[str, Any] | None, repository: Any) -> bool:
    """Whether the asking message still waits, as its holder and the place it opens at see it now."""

    from . import family_facade

    holder, message_id, place = str(row["source_resident_id"]), str(row["source_message_id"]), str(row["resident_id"])
    if place_record is None:
        return False
    if place != holder:
        # The facade must still show this member message, through a relay it keeps.
        found = family_facade.relayed_reply(place_record, message_id)
        if found is None or found["worker_id"] != holder:
            return False
        if row["kind"] != "question_set" and family_facade.project_input_state(
                place_record, found["request_message_id"], {}).get("state") == "cancelled":
            return False
    if row["kind"] == "question_set":
        try:
            return repository.question_set_state(holder, message_id) == "open"
        except ValueError:
            return False
    message = repository.message(holder, message_id)
    return message is not None and _native_waiting(holder, message, repository)


def _may_answer(record: Mapping[str, Any], kind: str, actor: str) -> bool:
    """The User may open the Conversation there and give the answer it takes."""

    from management_portal import permission_system
    from . import resident_access

    arguments = {"resident_id": record.get("resident_id")}
    if not permission_system.allows(actor, "GetConversation", arguments):
        return False
    if kind == "question_set":
        return (resident_access.resident_deliverable_to_actor(dict(record), actor)
                and permission_system.allows(actor, "DeliverMessage", arguments))
    return (resident_access.can_manage_resident(dict(record), actor)
            and permission_system.allows(actor, "AnswerResidentRequest", arguments))


def list_user_pending_requests(actor: str) -> dict[str, list[dict[str, Any]]]:
    """``{"requests": [...]}``: every question waiting for the calling User's answer that they may answer, oldest first.

    Raises ``IndexNotReady`` rather than return a list that could miss one.
    """

    from . import resident_conversation_stream, resident_record_domain
    from .state_store import get_lineage_data_store

    user = canonical_actor_id(actor)
    if not user or actor_principal(user).kind != "user":
        return {"requests": []}
    store = get_lineage_data_store()
    repository = resident_conversation_stream.get_repository()
    _repair(store, repository)
    with store.database.read() as connection:
        rows = connection.execute(
            f"SELECT {COLUMNS} FROM {TABLE} WHERE user_id=%s ORDER BY created_at, source_message_id",
            (_user_key(user),),
        ).fetchall()
    records: dict[str, dict[str, Any] | None] = {}
    finished: list[tuple[str, str, str]] = []
    result: list[dict[str, Any]] = []
    for row in rows:
        place = str(row["resident_id"])
        if place not in records:
            records[place] = resident_record_domain.find_resident(place)
        place_record = records[place]
        try:
            waiting = _waiting(row, place_record, repository)
        except KeyError:
            waiting = False
        if not waiting:
            finished.append((row["user_id"], row["source_resident_id"], row["source_message_id"]))
            continue
        if not _may_answer(place_record, row["kind"], user):
            continue
        created_at = row["created_at"]
        result.append({
            "key": request_key(row["source_resident_id"], row["source_message_id"]),
            "source_resident_id": row["source_resident_id"],
            "source_message_id": row["source_message_id"],
            "resident_id": place,
            "message_id": row["message_id"],
            "kind": row["kind"],
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            "title": str(place_record.get("title") or ""),
        })
    if finished:
        store.database.write(lambda connection: connection.execute(
            f"DELETE FROM {TABLE} WHERE (user_id, source_resident_id, source_message_id) IN "
            "(SELECT * FROM unnest(%s::text[], %s::text[], %s::text[]))",
            ([item[0] for item in finished], [item[1] for item in finished], [item[2] for item in finished]),
        ))
    return {"requests": result}


# A message's body is its Segment's payload: the stored fences or tool parts mark the candidates.
BACKFILL_MARKERS = [marker.encode() for marker in (
    "portal-question", "async_questions", *sorted(INTERACTIVE_TOOL_NAMES),
)]
# One batch of Chat positions after a key, in primary-key order, with the
# candidates among them; the batch's last key is where the next one starts.
BACKFILL_BATCH = (
    "WITH batch AS (SELECT sequence_id, source_kind, source_id, segment_type, message_id "
    " FROM chat_message_positions WHERE (sequence_id, source_kind, source_id) > (%s, %s, %s) "
    " ORDER BY sequence_id, source_kind, source_id LIMIT %s) "
    "SELECT batch.sequence_id, batch.source_kind, batch.source_id, agent.agent_id, batch.message_id, "
    "batch.segment_type='model' AND batch.source_kind='segment' AND batch.message_id IS NOT NULL "
    "AND EXISTS (SELECT 1 FROM segments segment JOIN payloads payload ON payload.payload_ref=segment.content_ref "
    " WHERE segment.segment_id=batch.source_id AND EXISTS (SELECT 1 FROM unnest(%s::bytea[]) marker "
    " WHERE position(marker IN payload.body) > 0)) AS candidate "
    "FROM batch LEFT JOIN agents agent ON agent.trace_sequence_id=batch.sequence_id "
    "ORDER BY batch.sequence_id, batch.source_kind, batch.source_id"
)
BACKFILL_BATCH_SIZE = 500


def _progress(connection: Any) -> tuple[str, str, str] | None:
    """Where the backfill stands: None once ready, else the last key it indexed ('' keys at the start)."""

    row = connection.execute("SELECT value FROM lineage_meta WHERE key=%s", (READY_KEY,)).fetchone()
    value = str(row["value"]) if row else ""
    if value == "ready":
        return None
    parts = value.removeprefix("after:").split("\t") if value.startswith("after:") else []
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 else ("", "", "")


_BACKFILL_THREAD: Any = None
_BACKFILL_START = __import__("threading").Lock()


def start_backfill(*, work_seconds: float = 5.0, rest_seconds: float = 2.0) -> bool:
    """Keep the backfill going in one background thread of this process until ready; True once ready.

    SocietyService housekeeping calls it at start and each round: it starts
    the thread when none runs in this process, so a failed or stopped
    run resumes from its committed place next round.  The thread alternates
    ``work_seconds`` of batches with ``rest_seconds`` of rest and ends when
    the index is ready; housekeeping's own period is unchanged.
    """

    import threading
    import time

    global _BACKFILL_THREAD
    from .state_store import get_lineage_data_store

    with get_lineage_data_store().database.read() as connection:
        if _progress(connection) is None:
            return True
    with _BACKFILL_START:
        if _BACKFILL_THREAD is not None and _BACKFILL_THREAD.is_alive():
            return False

        def run() -> None:
            try:
                while not backfill_step(time_budget_seconds=work_seconds):
                    time.sleep(rest_seconds)
            except Exception:  # noqa: BLE001 - the next housekeeping round resumes from the committed place
                LOGGER.exception("pending-question backfill stopped; housekeeping resumes it")

        _BACKFILL_THREAD = threading.Thread(target=run, name="pending-question-backfill", daemon=True)
        _BACKFILL_THREAD.start()
    return False


def backfill_step(*, time_budget_seconds: float = 2.0) -> bool:
    """Index the stored messages of a store that had Conversations before the index; True once ready.

    SocietyService housekeeping calls it until it is done, so an upgraded
    instance needs no separate step.  Each batch is committed with its place,
    so a restart resumes and a repeat changes nothing; messages written since
    the index existed were indexed when written.  Only questions still waiting
    are indexed.
    """

    import time

    from . import resident_conversation_stream, resident_record_domain
    from .state_store import get_lineage_data_store

    store = get_lineage_data_store()
    with store.database.read() as connection:
        after = _progress(connection)
    if after is None:
        return True
    repository = resident_conversation_stream.get_repository()
    records: dict[str, dict[str, Any] | None] = {}
    names = ("user_id", "source_resident_id", "source_message_id", "resident_id", "message_id", "kind")

    def still_waiting(row: tuple[Any, ...]) -> bool:
        if row[3] not in records:
            records[row[3]] = resident_record_domain.find_resident(row[3])
        return _waiting(dict(zip(names, row)), records[row[3]], repository)

    deadline = time.monotonic() + time_budget_seconds
    while True:
        with repository.agent_data.database.read() as connection:
            batch = connection.execute(BACKFILL_BATCH, (*after, BACKFILL_BATCH_SIZE, BACKFILL_MARKERS)).fetchall()
        rows: list[tuple[Any, ...]] = []
        repairs: list[Pending] = []
        for position in batch:
            if not position["candidate"] or not position["agent_id"]:
                continue
            try:
                rows.extend(row for row in reindex(position["agent_id"], position["message_id"], repository)
                            if still_waiting(row))
            except Exception:  # noqa: BLE001 - left for the read's repair, which reports it
                LOGGER.exception("pending-question backfill left %s/%s for repair", position["agent_id"], position["message_id"])
                repairs.append(Pending(position["agent_id"], position["message_id"], None))
        done = len(batch) < BACKFILL_BATCH_SIZE
        if batch:
            last = batch[-1]
            after = (str(last["sequence_id"]), str(last["source_kind"]), str(last["source_id"]))

        def commit(connection: Any, rows: list[tuple[Any, ...]] = rows, repairs: list[Pending] = repairs,
                   after: tuple[str, str, str] = after, done: bool = done) -> None:
            record(connection, rows)
            for repair in repairs:
                repair.write(connection)
            connection.execute(
                "INSERT INTO lineage_meta(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (READY_KEY, "ready" if done else "after:" + "\t".join(after)),
            )

        store.database.write(commit)
        if done:
            LOGGER.info("pending-question index backfilled and ready")
            return True
        if time.monotonic() >= deadline:
            return False

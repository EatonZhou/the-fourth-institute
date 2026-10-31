"""The state of each input in a Resident Conversation, its replies and question sets."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from management_common.async_questions import QUESTION_SET_CLOSED, question_set
from management_common.database_time import iso_time, instant

from management_portal.execution.agent_data_store import AgentDataStore, utc_now

from .resident_conversation_events import ConversationEvents
from .resident_conversation_messages import ConversationMessages
from .resident_conversation_records import (
    INPUT_STATES,
    INPUT_STATE_CHANGED,
    MESSAGE_UPDATED,
    TERMINAL_INPUT_STATES,
    compact_json,
    final_reply,
    input_state_value,
    message_from_storage,
    required_resident_id,
)
from .state_store import LineageDataStore


class ConversationInputs:
    """Where each input stands, its final replies and the question sets that wait for an answer."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        events: ConversationEvents,
        messages: ConversationMessages,
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = events
        self.messages = messages

    def question_set_state(self, resident_id: object, message_id: object) -> str:
        """``answered``, ``closed`` or ``open``: the state of the question set an Agent message asks.

        A set is answered once its answer message exists and stays open until
        then: later messages, a person's included, and later question sets
        leave it answerable.  A person may close it unanswered instead; its
        answer slot then holds a cancelled input state.  The Chat derives the
        same state.
        """

        stream_id = required_resident_id(resident_id)
        source_id = str(message_id or "").strip()
        questions = question_set(self.messages.message(stream_id, source_id))
        if questions is None or self.agent_data.chat_message_ordinal(stream_id, source_id) is None:
            raise ValueError("该 Resident 中的问题不存在或已不可用")
        answer_id = questions["answer_message_id"]
        if self.messages.stored_message(stream_id, answer_id) is not None:
            return "answered"
        try:
            slot = self.input_state(stream_id, answer_id)
        except KeyError:
            return "open"
        return "closed" if (slot.get("error") or {}).get("code") == QUESTION_SET_CLOSED else "open"

    def close_question_set(self, resident_id: object, message_id: object, *, actor: str) -> str:
        """Close an open question set unanswered; nothing is sent to the Resident."""

        state = self.question_set_state(resident_id, message_id)
        if state == "answered":
            raise ValueError("这组问题已回答")
        if state == "open":
            questions = question_set(self.messages.message(resident_id, message_id))
            self.set_input_state(resident_id, questions["answer_message_id"], "cancelled", error={
                "code": QUESTION_SET_CLOSED, "message": "这组问题已关闭，不再回答", "closed_by": str(actor or ""),
            })
        return "closed"

    def set_input_state(
        self,
        resident_id: object,
        message_id: object,
        state: str,
        *,
        error: Mapping[str, Any] | None = None,
        updated_at: str = "",
    ) -> dict[str, Any]:
        """Record where an input stands.  Its replies are not given: they are
        the trace's messages answering it (``reply_to_message_id``)."""

        stream_id = required_resident_id(resident_id)
        target = str(message_id or "").strip()
        clean_state = str(state or "").strip().casefold()
        if not target:
            raise ValueError("Input State requires message_id")
        if clean_state not in INPUT_STATES:
            raise ValueError(f"Unsupported Input State: {clean_state}")

        def prepare_update(current: Any) -> tuple[dict[str, Any], bool]:
            previous = input_state_value(current) if current is not None else None
            stored = copy.deepcopy(previous)
            if previous is not None:
                self.with_outcomes_many({stream_id: [previous]})
            existing_error = dict((previous or {}).get("error") or {})
            error_value = dict(error) if error is not None else existing_error
            value = {
                "message_id": target,
                "state": clean_state,
                "updated_at": iso_time(instant(updated_at or utc_now())),
            }
            if error_value:
                value["error"] = error_value
            if previous is not None:
                if (
                    previous["state"] == value["state"]
                    and existing_error == error_value
                    and not updated_at
                ):
                    return previous, previous != stored
                if previous == value:
                    return value, previous != stored
                if (
                    previous["state"] in TERMINAL_INPUT_STATES
                    and previous["state"] != clean_state
                ):
                    # Execution snapshots and cancellation acknowledgements may
                    # arrive out of order. The first terminal state is durable;
                    # a stale projection must not break later message/activity
                    # events or reopen the input.
                    return previous, False
            return value, True

        def write(connection: Any) -> dict[str, Any]:
            self.events.ensure_conversation(connection, stream_id)
            current = connection.execute(
                'SELECT * FROM conversation_input_states WHERE resident_id = %s AND message_id = %s',
                (stream_id, target),
            ).fetchone()
            value, changed = prepare_update(current)
            if not changed:
                return value
            connection.execute(
                'INSERT INTO conversation_input_states(resident_id, message_id, state, error_json, updated_at) '
                'VALUES (%s, %s, %s, %s, %s) ON CONFLICT(resident_id, message_id) DO UPDATE SET '
                'state=excluded.state, error_json=excluded.error_json, updated_at=excluded.updated_at',
                (stream_id, target, clean_state, compact_json(value.get("error", {})), value["updated_at"]),
            )
            if clean_state in TERMINAL_INPUT_STATES:
                # Usage is read from the now-settled Execution. The selected
                # reply may already have streamed before continuation ended;
                # publish its header again so live readers rehydrate the footer.
                replies = self.agent_data.chat_final_replies_many({stream_id: [target]})
                for reply_id in final_reply(clean_state, replies.get(stream_id, {}).get(target)):
                    self.events.record_event(connection, stream_id, MESSAGE_UPDATED,
                                       {"message": {"message_id": reply_id}}, subject_id=reply_id)
            self.events.record_event(
                connection,
                stream_id,
                INPUT_STATE_CHANGED,
                {"input_state": value},
                subject_id=target,
            )
            if clean_state in TERMINAL_INPUT_STATES:
                # An input that ended undelivered (withdrawn, stopped) leaves the Mailbox.
                self.state_store.mailbox_forget(stream_id, [target], places=("pending",), connection=connection)
                # Temporary credentials lent with this message last as long as its input (decision_b820faf42218 §5.6).
                from management_portal import permission_system

                permission_system.end_task_credentials(connection, stream_id, target)
            return value

        with self.events.protocol_scope(stream_id):
            with self.state_store.database.read_current() as connection:
                current = connection.execute(
                    'SELECT inputs.* FROM conversation_input_states AS inputs JOIN conversation_states AS conversations USING (resident_id) WHERE inputs.resident_id = %s AND inputs.message_id = %s',
                    (stream_id, target),
                ).fetchone()
            value, changed = prepare_update(current) if current is not None else ({}, True)
            if changed:
                value = self.state_store.database.write(write)
        return self._with_replies(stream_id, [value])[0]

    def publish_archived_input_state(self, resident_id: str, message_id: str) -> None:
        """Finish the input projection after its terminal turn has been archived.

        The receipt lookup and commit share the Conversation scope with requeue;
        a reopened input or new active turn must retain its current state.
        """
        with self.events.protocol_scope(resident_id):
            try:
                value = self.input_state(resident_id, message_id)
            except KeyError:
                return
            if value["state"] in TERMINAL_INPUT_STATES:
                self.set_input_state(resident_id, message_id, value["state"],
                                     error=value.get("error", {}), updated_at=value["updated_at"])

    def _with_replies(self, resident_id: str, values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give each input state its replies: the final reply of a finished input, from the trace."""

        self._with_replies_many({resident_id: values})
        return list(values)

    def with_outcomes_many(self, values_by_resident: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        terminal = self.state_store.terminal_input_receipts([
            (resident_id, str(value['message_id']))
            for resident_id, values in values_by_resident.items() for value in values
            if value.get('state') in {'queued', 'running'}
        ])
        for resident_id, values in values_by_resident.items():
            for value in values:
                receipt = terminal.get((resident_id, str(value['message_id'])), {})
                if receipt.get('outcome') in TERMINAL_INPUT_STATES:
                    value.update(state=receipt['outcome'], updated_at=receipt['finished_at'])
                    if receipt['outcome'] == 'failed':
                        value['error'] = dict(receipt.get('runtime_error') or value.get('error') or {
                            'code': 'execution_failed', 'message': receipt.get('error') or 'Resident execution failed',
                        })

    def _with_replies_many(self, values_by_resident: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        self.with_outcomes_many(values_by_resident)
        self._fill_replies_many(values_by_resident)

    def _fill_replies_many(self, values_by_resident: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        finished = {
            resident_id: [str(value["message_id"]) for value in values if value.get("state") in TERMINAL_INPUT_STATES]
            for resident_id, values in values_by_resident.items()
        }
        replies = self.agent_data.chat_final_replies_many(
            {resident_id: ids for resident_id, ids in finished.items() if ids},
        )
        for resident_id, values in values_by_resident.items():
            for value in values:
                value["reply_message_ids"] = final_reply(
                    value.get("state"), replies.get(resident_id, {}).get(str(value["message_id"])),
                )

    def reopen_input(self, resident_id: object, message_id: object) -> dict[str, Any]:
        """Put a finished input back to ``queued`` for a Mailbox requeue (decision_15f7df737ed5).

        Every other writer keeps an input's first terminal state (see
        ``set_input_state``); only putting a stopped or failed round back
        reopens one.  The reopened input has no error, and the Conversation
        keeps the notices of the round that ended.
        """

        stream_id = required_resident_id(resident_id)
        target = str(message_id or "").strip()
        if not target:
            raise ValueError("Input State requires message_id")

        def write(connection: Any) -> dict[str, Any]:
            self.events.ensure_conversation(connection, stream_id)
            current = connection.execute(
                'SELECT state, updated_at FROM conversation_input_states WHERE resident_id = %s AND message_id = %s',
                (stream_id, target),
            ).fetchone()
            if current is None:
                raise KeyError(target)
            observed = {"message_id": target, "state": str(current["state"]),
                        "updated_at": iso_time(current["updated_at"])}
            self.with_outcomes_many({stream_id: [observed]})
            if observed["state"] == "queued":
                return observed
            if observed["state"] not in TERMINAL_INPUT_STATES:
                raise ValueError(f"Input {target} is {current['state']}, not finished")
            # A requeue supersedes the receipt even within the same clock tick.
            reopened_at = max(datetime.fromisoformat(utc_now().replace("Z", "+00:00")),
                              datetime.fromisoformat(observed["updated_at"].replace("Z", "+00:00")) + timedelta(milliseconds=1))
            value = {"message_id": target, "state": "queued",
                     "updated_at": iso_time(reopened_at)}
            connection.execute(
                'UPDATE conversation_input_states SET state = %s, error_json = %s, '
                'updated_at = %s WHERE resident_id = %s AND message_id = %s',
                ("queued", compact_json({}), value["updated_at"], stream_id, target),
            )
            self.events.record_event(connection, stream_id, INPUT_STATE_CHANGED, {"input_state": value}, subject_id=target)
            return value

        with self.events.protocol_scope(stream_id):
            value = self.state_store.database.write(write)
        return self._with_replies(stream_id, [value])[0]

    def record_accepted_input(self, connection, resident_id: str, message_id: str) -> None:
        """Commit the initial receipt/event with admission, without reading projections."""
        connection.execute("INSERT INTO conversation_states(resident_id) VALUES (%s) ON CONFLICT DO NOTHING", (resident_id,))
        row = connection.execute(
            "INSERT INTO conversation_input_states(resident_id,message_id,state,updated_at) "
            "VALUES (%s,%s,'queued',%s) ON CONFLICT DO NOTHING RETURNING updated_at",
            (resident_id, message_id, utc_now()),
        ).fetchone()
        if row is not None:
            self.events.record_event(connection, resident_id, INPUT_STATE_CHANGED,
                               {"input_state": {"message_id": message_id, "state": "queued", "updated_at": iso_time(row["updated_at"])}},
                               subject_id=message_id)

    def input_state(self, resident_id: object, message_id: object) -> dict[str, Any]:
        key = (required_resident_id(resident_id), str(message_id or "").strip())
        found = self.input_states_by_id([key])
        if key not in found:
            raise KeyError(key[1])
        return found[key]

    def input_states_by_id(
        self, requests: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Resolve the requested input states and terminal receipts in batches."""
        requested = list(dict.fromkeys(requests))
        if not requested:
            return {}
        with self.state_store.database.read() as connection:
            rows = connection.execute(
                "SELECT state.* FROM conversation_input_states state "
                "JOIN unnest(%s::text[],%s::text[]) requested(resident_id,message_id) "
                "USING(resident_id,message_id)",
                ([resident for resident, _ in requested], [message for _, message in requested]),
            ).fetchall()
        values: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            values.setdefault(str(row["resident_id"]), []).append(input_state_value(row))
        self._with_replies_many(values)
        return {(resident, value["message_id"]): value for resident, items in values.items() for value in items}

    def reply_rounds(
        self, requests: Sequence[tuple[str, str]], *, limit: int = 20,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Selected rounds, retained replies, and cancellation before delivery (withdrawal)."""
        requested = list(dict.fromkeys(requests))
        states = self.input_states_by_id(requested)
        windows: dict[str, dict[str, str]] = {}
        final_ids: dict[tuple[str, str], list[str]] = {}
        for resident, message in requested:
            state = states.get((resident, message), {})
            final = list(state.get("reply_message_ids") or []) if state.get("state") in TERMINAL_INPUT_STATES else []
            final_ids[(resident, message)] = final
            windows.setdefault(resident, {})[message] = final[-1] if final else ""
        replies = self.agent_data.chat_replies_many(windows, limit=limit)
        missing: dict[str, list[str]] = {}
        for key, final in final_ids.items():
            resident, message = key
            shown = {item["message_id"] for item in replies.get(resident, {}).get(message, [])}
            missing.setdefault(resident, []).extend(identity for identity in final[:limit] if identity not in shown)
            if states.get(key, {}).get("state") == "cancelled":
                # Pending inputs disappear when withdrawn. Check the durable
                # trace, not today's Chat: clearing Chat must not turn a
                # stopped execution into a withdrawal.
                missing[resident].append(message)
        # A context reset hides old replies from Chat, but a finished Family
        # round still names its retained ending in the member's trace.
        retained = {resident: self.agent_data.trace_chat_messages(resident, ids)
                    for resident, ids in missing.items() if ids}
        result = {}
        for key in requested:
            resident, message = key
            outputs = list(replies.get(resident, {}).get(message, []))
            shown = {item["message_id"] for item in outputs}
            outputs.extend(retained[resident][identity] for identity in final_ids[key][:limit]
                           if identity not in shown and identity in retained.get(resident, {}))
            result[key] = {"input_state": states.get(key, {}),
                           "replies": [message_from_storage(item) for item in outputs]}
            if (states.get(key, {}).get("state") == "cancelled"
                    and message not in retained.get(resident, {}) and not outputs):
                result[key]["withdrawn"] = True
        return result

    def input_states(
        self,
        resident_id: object,
        *,
        states: Sequence[str] = (),
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        stream_id = required_resident_id(resident_id)
        requested_states = tuple(dict.fromkeys(
            str(item or "").strip().lower()
            for item in states
            if str(item or "").strip()
        ))
        if any(item not in INPUT_STATES for item in requested_states):
            raise ValueError("Invalid Resident input state")
        safe_limit = max(0, int(limit or 0))
        # Pending observations can have an archived terminal round. Resolve all
        # pending candidates before filtering; bound the already-terminal branch
        # independently so a limit cannot hide a newer recovered outcome.
        terminal_states = sorted(set(requested_states or INPUT_STATES) & TERMINAL_INPUT_STATES)
        statement = "SELECT * FROM conversation_input_states WHERE resident_id = %s AND state IN ('queued','running')"
        parameters: list[object] = [stream_id]
        if terminal_states:
            terminal_query = (
                "SELECT * FROM conversation_input_states WHERE resident_id = %s "
                "AND state = ANY(%s) ORDER BY updated_at DESC, message_id DESC"
            )
            parameters.extend([stream_id, terminal_states])
            if safe_limit:
                terminal_query += " LIMIT %s"
                parameters.append(safe_limit)
            statement = "(" + statement + ") UNION ALL (" + terminal_query + ")"
        with self.state_store.database.read() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        values = [input_state_value(row) for row in rows]
        self.with_outcomes_many({stream_id: values})
        values = [value for value in values if not requested_states or value["state"] in requested_states]
        values.sort(key=lambda value: (value["updated_at"], value["message_id"]), reverse=True)
        if safe_limit:
            values = values[:safe_limit]
        self._fill_replies_many({stream_id: values})
        return values

    def latest_notifiable_input_states_for_messages(
        self,
        message_ids_by_resident: Mapping[object, object],
    ) -> dict[str, dict[str, Any]]:
        """Read the latest reply or failure outcome, excluding control receipts."""

        requested = {
            required_resident_id(resident_id): tuple(dict.fromkeys(
                str(message_id or "").strip()
                for message_id in (
                    message_ids
                    if isinstance(message_ids, (list, tuple, set, frozenset))
                    else [message_ids]
                )
                if str(message_id or "").strip()
            ))
            for resident_id, message_ids in message_ids_by_resident.items()
            if str(resident_id or "").strip()
        }
        requested = {key: value for key, value in requested.items() if value}
        if not requested:
            return {}
        pairs = [
            (resident_id, message_id)
            for resident_id, message_ids in requested.items()
            for message_id in message_ids
        ]
        # One join over the requested pairs: a user's Mailbox asks for tens of
        # thousands, and chunks of OR-ed pairs cost a planned statement each.
        with self.state_store.database.read() as connection:
            rows = connection.execute(
                "SELECT stored.* FROM unnest(%s::text[], %s::text[]) AS requested(resident_id, message_id) "
                "JOIN conversation_input_states stored USING (resident_id, message_id)",
                ([resident_id for resident_id, _ in pairs], [message_id for _, message_id in pairs]),
            ).fetchall()
        values_by_resident: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            values_by_resident.setdefault(str(row["resident_id"]), []).append(input_state_value(row))
        self.with_outcomes_many(values_by_resident)
        values_by_resident = {
            resident_id: [value for value in values if value["state"] in TERMINAL_INPUT_STATES]
            for resident_id, values in values_by_resident.items()
        }
        values_by_resident = {resident_id: values for resident_id, values in values_by_resident.items() if values}
        # Start at the newest candidate. If it is only a control receipt, widen
        # the bounded window so long stretches of receipts do not cause one
        # database round trip per historical input. Stop each Resident at its
        # newest real outcome; ordinary conversations still resolve one input.
        # Each round costs a few fixed queries, so the window grows fast:
        # scheduled Residents and facades end many inputs with receipts only.
        for values in values_by_resident.values():
            values.sort(key=lambda value: (str(value["updated_at"]), str(value["message_id"])))
        result: dict[str, dict[str, Any]] = {}
        batch_size = 1
        while values_by_resident:
            batch = {resident_id: [values.pop() for _ in range(min(batch_size, len(values)))]
                     for resident_id, values in values_by_resident.items()}
            self._fill_replies_many(batch)
            reply_ids = list(dict.fromkeys(
                str(reply_id) for values in batch.values() for value in values
                if value["state"] == "completed" for reply_id in value["reply_message_ids"]
            ))
            agent_replies = {
                (header["agent_id"], header["message_id"]): header["reply_to_message_id"]
                for header in self.agent_data.chat_message_headers(list(batch), message_ids=reply_ids)
                if header["role"] == "agent" and header["has_content"]
            } if reply_ids else {}
            for resident_id, values in batch.items():
                for value in values:
                    if value["state"] == "completed":
                        value["reply_message_ids"] = [
                            reply_id for reply_id in value["reply_message_ids"]
                            if agent_replies.get((resident_id, reply_id)) == value["message_id"]
                        ]
                    if value["state"] != "completed" or value["reply_message_ids"]:
                        result[resident_id] = value
                        break
                if resident_id in result or not values_by_resident[resident_id]:
                    del values_by_resident[resident_id]
            batch_size = min(batch_size * 8, 64)
        return result

    def input_states_for_snapshot(
        self,
        connection: Any,
        resident_id: str,
        message_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(message_ids))
        active_states = sorted(INPUT_STATES - TERMINAL_INPUT_STATES)
        terminal_states = sorted(TERMINAL_INPUT_STATES)
        # Active inputs are needed even when their messages are off-page.
        # Seek them by state; do not read every historical terminal payload.
        rows = connection.execute(
            "SELECT * FROM conversation_input_states WHERE resident_id = %s "
            f"AND state IN ({','.join('%s' for _ in active_states)})",
            (resident_id, *active_states),
        ).fetchall()
        for offset in range(0, len(requested), 500):
            batch = requested[offset:offset + 500]
            rows.extend(connection.execute(
                "SELECT * FROM conversation_input_states WHERE resident_id = %s "
                f"AND message_id IN ({','.join('%s' for _ in batch)}) "
                f"AND state IN ({','.join('%s' for _ in terminal_states)})",
                (resident_id, *batch, *terminal_states),
            ).fetchall())
        rows.sort(key=lambda row: (row["updated_at"], str(row["message_id"])))
        return self._with_replies(resident_id, [input_state_value(row) for row in rows])

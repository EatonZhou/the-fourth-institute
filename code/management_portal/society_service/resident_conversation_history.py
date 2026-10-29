"""Reading a Resident Conversation's history: pages, replies, input history and search."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from management_common.async_questions import visible_message_input_ids

from management_portal.execution.agent_data_store import AgentDataStore

from .resident_conversation_inputs import ConversationInputs
from .resident_conversation_messages import ConversationMessages
from .resident_conversation_records import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    message_from_storage,
    required_resident_id,
)
from .state_store import LineageDataStore


class ConversationHistory:
    """Read-only pages and searches of the messages of Resident Conversations."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        messages: ConversationMessages,
        inputs: ConversationInputs,
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.messages = messages
        self.inputs = inputs

    def read_messages(self, resident_id: object, *, limit: int = 0) -> list[dict[str, Any]]:
        return [
            message_from_storage(message)
            for message in self.agent_data.read_chat_messages(
                required_resident_id(resident_id),
                limit=limit,
            )
        ]

    def replies(
        self,
        resident_id: object,
        message_id: object,
        *,
        until_message_id: object = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """The Agent messages answering an input, intermediate outputs included, oldest first."""

        return [
            message_from_storage(message)
            for message in self.agent_data.chat_replies(
                required_resident_id(resident_id),
                str(message_id or "").strip(),
                until_message_id=str(until_message_id or "").strip(),
                limit=limit,
            )
        ]

    def latest_message(
        self,
        resident_id: object,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        stream_id = required_resident_id(resident_id)
        stored = self.agent_data.latest_chat_message(stream_id, role=role)
        if stored is not None and stored.get("grouped_into_message_id"):
            # A group's members come just before it: the group is the newest message.
            stored = self.agent_data.chat_message(stream_id, str(stored["grouped_into_message_id"])) or stored
        return message_from_storage(stored) if stored is not None else None

    def public_messages(
        self,
        resident_id: object,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.read_messages(resident_id, limit=limit)

    def messages_before_input(self, resident_id: str, message_id: str, *, limit: int) -> list[dict]:
        """Bound history to a durable input identity and the current context reset."""
        return [message_from_storage(message) for message in self.agent_data.read_chat_messages_through_turn(
            required_resident_id(resident_id), [message_id], limit=limit, include_turn=False,
        )]

    def messages_through_turn(
        self,
        resident_id: object,
        message_ids: Sequence[object],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return [
            message_from_storage(message)
            for message in self.agent_data.read_chat_messages_through_turn(
                required_resident_id(resident_id),
                message_ids,
                limit=limit,
            )
        ]

    def messages_page(
        self,
        resident_id: object,
        page_token: object,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        stream_id = required_resident_id(resident_id)
        safe_limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
        try:
            before_ordinal = int(str(page_token or "").strip())
        except ValueError as exc:
            raise ValueError("Invalid Conversation page token") from exc
        if before_ordinal <= 0:
            raise ValueError("Invalid Conversation page token")
        page = self.agent_data.read_chat_message_page(
            stream_id,
            before_ordinal=before_ordinal,
            limit=safe_limit,
        )
        next_boundary = page.get("next_before_ordinal")
        messages = self.messages.tool_status_messages([message_from_storage(item) for item in page.get("messages") or []], resident_id=stream_id)
        with self.state_store.database.read() as connection:
            input_states = self.inputs.input_states_for_snapshot(
                connection, stream_id, visible_message_input_ids(messages),
            )
        return {
            "resident_id": stream_id,
            "messages": messages,
            "input_states": input_states,
            "next_page_token": str(next_boundary) if next_boundary else "",
        }

    def input_history_page(
        self,
        resident_id: object,
        sender: object,
        page_token: object = "",
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        """What ``sender`` typed in the Resident's conversation, newest first, for the input history."""

        stream_id = required_resident_id(resident_id)
        safe_limit = max(1, min(int(limit or 50), MAX_HISTORY_LIMIT))
        token = str(page_token or "").strip()
        before_ordinal = None
        if token:
            try:
                before_ordinal = int(token)
            except ValueError as exc:
                raise ValueError("Invalid input page token") from exc
            if before_ordinal <= 0:
                raise ValueError("Invalid input page token")
        page = self.agent_data.read_input_history(
            stream_id, str(sender or ""), before_ordinal=before_ordinal, limit=safe_limit,
        )
        next_boundary = page.get("next_before_ordinal")
        return {
            "resident_id": stream_id,
            "messages": [message_from_storage(item) for item in page.get("messages") or []],
            "next_page_token": str(next_boundary) if next_boundary else "",
        }

    def search_messages(
        self,
        resident_ids: Sequence[object],
        query: object,
        *,
        page_token: object = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        requested = [
            required_resident_id(item)
            for item in resident_ids
            if str(item or "").strip()
        ]
        needle = str(query or "").strip().casefold()
        if not needle:
            raise ValueError("Conversation search query required")
        safe_limit = max(1, min(int(limit or 50), 200))
        try:
            offset = int(str(page_token or "0").strip() or "0")
        except ValueError as exc:
            raise ValueError("Invalid Conversation search page token") from exc
        if offset < 0:
            raise ValueError("Invalid Conversation search page token")
        matches: list[dict[str, Any]] = []
        def conversations():
            for start in range(0, len(requested), 50):
                batch = requested[start:start + 50]
                histories = self.agent_data.read_chat_messages_many(batch)
                for stream_id in batch:
                    yield stream_id, histories[stream_id]

        for stream_id, messages in conversations():
            for index, stored in enumerate(messages):
                message = message_from_storage(stored)
                searchable = "\n".join(
                    str(part.get("text") or "")
                    for part in message.get("content_parts") or []
                    if isinstance(part, Mapping) and str(part.get("type") or "") == "text"
                ).casefold()
                if needle not in searchable:
                    continue
                matches.append({
                    "resident_id": stream_id,
                    "message": message,
                    "previous_message_id": (
                        str(messages[index - 1].get("message_id") or "")
                        if index > 0
                        else ""
                    ),
                    "next_message_id": (
                        str(messages[index + 1].get("message_id") or "")
                        if index + 1 < len(messages)
                        else ""
                    ),
                })
        matches.sort(
            key=lambda item: (
                str((item.get("message") or {}).get("created_at") or ""),
                str((item.get("message") or {}).get("message_id") or ""),
            ),
            reverse=True,
        )
        selected = matches[offset:offset + safe_limit]
        next_offset = offset + len(selected)
        return {
            "query": str(query or "").strip(),
            "results": selected,
            "next_page_token": str(next_offset) if next_offset < len(matches) else "",
        }

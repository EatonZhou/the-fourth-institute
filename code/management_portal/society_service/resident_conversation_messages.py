"""Messages of a Resident Conversation: committing, appending, reading and updating them."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from management_portal.execution.agent_data_store import AgentDataStore, chat_segment_id

from .resident_conversation_events import ConversationEvents
from .resident_conversation_records import (
    DISPLAY_FIELDS,
    MESSAGE_APPENDED,
    MESSAGE_UPDATED,
    immutable_message_fields,
    message_for_storage,
    message_from_storage,
    message_header,
    protocol_message,
    required_message_id,
    required_resident_id,
)
from . import resident_message_contract
from .state_store import LineageDataStore


class ConversationMessages:
    """The messages of each Resident Conversation: accepted into its Mailbox, delivered into its trace."""

    def __init__(
        self,
        state_store: LineageDataStore,
        agent_data: AgentDataStore,
        events: ConversationEvents,
    ) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = events

    def commit_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        if protocol_message(message)["role"] != "user":
            raise ValueError("Only user messages use the intake commit protocol")
        return self.append_message(
            resident_id,
            message,
            activated_at=activated_at,
        )

    def accept_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Keep a Message that waits in the Mailbox; it enters the trace when it is delivered."""

        stream_id = required_resident_id(resident_id)
        public_input = protocol_message(message)
        if public_input["role"] != "user":
            raise ValueError("Only user messages use the intake acceptance protocol")
        public_input.pop("activated_at", None)
        payload = message_for_storage(public_input, private_fields=message)
        shown = self.agent_data.chat_message(stream_id, str(public_input["message_id"]))
        if shown is not None:
            stored = shown
        else:
            with self.events.protocol_scope(stream_id):
                stored, _inserted = self.state_store.mailbox_accept(stream_id, payload)
        return self._validate_stored_message_identity(stored, public_input)

    def validate_message_identity(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate an idempotent replay without mutating the stream or storage."""

        public_input = protocol_message(message)
        stored = self.stored_message(resident_id, public_input["message_id"])
        if stored is None:
            raise KeyError(str(public_input["message_id"]))
        return self._validate_stored_message_identity(stored, public_input)

    def _validate_stored_message_identity(
        self, stored: Mapping[str, Any], public_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Check the accepted input's immutable ancestry, returning its current view."""
        public = message_from_storage(stored)
        expected = immutable_message_fields(public_input)
        if immutable_message_fields(public) == expected:
            return public
        # Harness preparation may replace the displayed input. Acceptance,
        # read-only validation and delivery must all compare the original
        # envelope against the same provenance chain, never only the wrapper.
        if stored.get("segment_id") and public_input["role"] == "user":
            for ancestor in self.agent_data.chat_message_ancestors(
                str(stored["segment_id"]), str(public_input["message_id"]),
            ):
                if immutable_message_fields(message_from_storage(ancestor)) == expected:
                    return public
        raise resident_message_contract.MessageIdentityConflict(
            f"Conversation message identity refers to different immutable content: {public_input['message_id']}"
        )

    def execution_content_parts(
        self,
        resident_id: object,
        execution_id: str,
        content_parts: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        stream_id = required_resident_id(resident_id)
        parts = copy.deepcopy([dict(part) for part in content_parts])
        identity_fields = (
            "type", "tool_name", "segment_id", "result_segment_id",
            "execution_id", "request_id", "thread_id", "turn_id", "item_id",
            "is_blocking", "status",
        )
        for index, part in enumerate(parts):
            if part.get("type") in {"native_turn", "resource_changes", "subagent_run"}:
                source = (self.agent_data.operation_record if part.get("type") in {"native_turn", "resource_changes", "subagent_run"} else self.agent_data.segment)(str(part.get("segment_id") or ""))
                canonical = source.get("content") or {}
                segment_type = "model" if part["type"] == "native_turn" else "artifact"
                if (source.get("created_by_agent_id") != stream_id or source.get("execution_id") != execution_id
                        or source.get("segment_type") != segment_type or canonical.get("type") != part["type"]
                        or any(canonical.get(key) != part.get(key) for key in ("segment_id", "execution_id", "thread_id", "turn_id"))):
                    raise ValueError("turn artifact must reference its own canonical Segment")
                parts[index] = copy.deepcopy(canonical)
                continue
            if part.get("tool_name") not in {"request_user_input", "request_approval", "mcp_elicitation"}:
                continue
            source = (self.agent_data.operation_record if part.get("type") in {"native_turn", "resource_changes", "subagent_run"} else self.agent_data.segment)(str(part.get("segment_id") or ""))
            canonical = source.get("content")
            if (
                not execution_id
                or source.get("segment_type") != "tool"
                or source.get("created_by_agent_id") != stream_id
                or source.get("execution_id") != execution_id
                or part.get("execution_id") != execution_id
                or part.get("type") not in {"tool_call", "tool_result"}
                or not isinstance(canonical, Mapping)
                or not canonical.get("request_id")
                or any(canonical.get(key) != part.get(key) for key in identity_fields)
            ):
                raise ValueError("tool projection must reference its own canonical Execution Segment")
            parts[index] = copy.deepcopy(dict(canonical))
        return parts

    def append_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        """Deliver a message: put it at the end of the Resident's trace, and so of its Chat.

        A message that waited in the Mailbox leaves it; one already delivered
        returns its current version. A Mailbox group consumes its member
        occurrences and keeps their ancestry outside the current trace.
        """

        stream_id = required_resident_id(resident_id)
        public_input = protocol_message(message)
        public_input.pop("activated_at", None)
        message_id = str(public_input["message_id"])

        def check(stored: Mapping[str, Any] | None) -> None:
            if stored is not None:
                self._validate_stored_message_identity(stored, public_input)

        # Fully committed replays are reads, not another protocol transaction.
        # A message without its durable event still enters the single writer
        # below, so recovery of a partially committed append is unchanged.
        existing = self.agent_data.chat_message(stream_id, message_id)
        if (existing is not None
                and str(message.get("execution_id") or "")
                and existing.get("execution_id") == message.get("execution_id")
                and str(existing.get("segment_id") or "") != chat_segment_id(stream_id, message_id)
                and ((public_input["role"] == "agent" and existing.get("author_id") == stream_id)
                     or (public_input["role"] == "system" and existing.get("harness_input")
                         and existing.get("harness_input") == message.get("harness_input")))
                and self.agent_data.has_segment(str(existing["segment_id"]))):
            # Execution's display event announces already collected content.
            # Use that current version, including any Harness transformation.
            public_input = protocol_message(message_from_storage(existing))
            public_input.pop("activated_at", None)
        check(existing)
        if existing is not None:
            with self.state_store.database.read() as connection:
                if self.events.event_exists(connection, stream_id, MESSAGE_APPENDED, message_id):
                    # A requeued message that ran again leaves the Mailbox now.
                    waiting = self.state_store.mailbox_entry(stream_id, message_id)
                    if waiting is not None and waiting["place"] == "pending":
                        self.state_store.mailbox_forget(stream_id, [message_id], places=("pending",))
                    return message_from_storage(existing)

        with self.events.protocol_scope(stream_id):
            waiting = self.state_store.mailbox_entry(stream_id, message_id)
            waiting_message = (waiting or {}).get("message") or {}
            check(waiting_message if waiting_message.get("content_parts") is not None else None)
            # What the Mailbox kept (a business reference) stays with the message.
            payload = message_for_storage(
                public_input, private_fields={**waiting_message, **dict(message)},
            )
            members = self._group_members(stream_id, payload)
            with self.agent_data.database.transaction():
                for member in members:
                    self.agent_data.project_chat_message(stream_id, member, activated_at=activated_at)
                persisted, _changed = self.agent_data.project_chat_message(
                    stream_id,
                    payload,
                    activated_at=activated_at,
                )
                if members:
                    self.agent_data.merge_message_group(
                        stream_id, [member["message_id"] for member in members], persisted["segment_id"],
                    )
            public = message_from_storage(persisted)

            def write(connection: Any) -> None:
                self.events.record_event(
                    connection,
                    stream_id,
                    MESSAGE_APPENDED,
                    {"message": message_header(public)},
                    subject_id=message_id,
                    once=True,
                )
                self.state_store.mailbox_forget(
                    stream_id, [message_id, *(str(item["message_id"]) for item in members)],
                    connection=connection,
                )

            self.state_store.database.write(write)
        return public

    def _group_members(self, resident_id: str, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The members a Mailbox group message delivers, each marked with its group."""

        group_id = str(message.get("message_id") or "")
        member_ids = [
            str(member.get("message_id") or "")
            for part in message.get("content_parts") or []
            if isinstance(part, Mapping) and part.get("type") == "mailbox"
            for member in part.get("members") or []
            if isinstance(member, Mapping) and member.get("message_id")
        ]
        if not member_ids or message.get("role") != "user":
            return []
        waiting = {
            entry["message_id"]: entry["message"]
            for entry in self.state_store.mailbox_entries([resident_id], message_ids=member_ids)
        }
        members: list[dict[str, Any]] = []
        for member_id in member_ids:
            stored = waiting.get(member_id)
            if not isinstance(stored, Mapping) or not isinstance(stored.get("content_parts"), list):
                stored = (self.agent_data.chat_message(resident_id, member_id)
                          or self.agent_data.group_source_message(resident_id, member_id))
                if stored is None:
                    raise ValueError(f"Mailbox group {group_id} lost its member {member_id}")
            members.append(message_for_storage(
                {key: value for key, value in stored.items() if key != "activated_at"},
                private_fields={**stored, "grouped_into_message_id": group_id},
            ))
        return members

    def tool_status_messages(self, messages: list[dict[str, Any]], *, resident_id: str) -> list[dict[str, Any]]:
        for message in messages:
            if message.get("role") != "agent":
                continue
            for part in message.get("content_parts") or []:
                if part.get("type") != "tool_call" or part.get("tool_name") not in {"request_user_input", "request_approval", "mcp_elicitation"}:
                    continue
                source = (self.agent_data.operation_record if part.get("type") in {"native_turn", "resource_changes", "subagent_run"} else self.agent_data.segment)(str(part.get("segment_id") or ""))
                if source.get("created_by_agent_id") != resident_id:
                    part["status"] = "expired"
                    continue
                try:
                    result = self.agent_data.segment(str(part.get("result_segment_id") or ""))
                except KeyError:
                    result = {}
                content = result.get("content") or {}
                if content:
                    part["status"] = content.get("status", "expired")
                    part["answers"] = content.get("answers", {})
                else:
                    execution = self.agent_data.execution(str(part.get("execution_id") or "")) or {}
                    turn_id = str(execution.get("provider_turn_id") or "")
                    if execution.get("status") in {"completed", "failed", "cancelled"} or (turn_id and turn_id != part.get("turn_id")):
                        part["status"] = "expired"
        return messages

    def message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        stored = self.stored_message(resident_id, message_id)
        return message_from_storage(stored) if stored is not None else None

    def stored_message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        """A message of the Resident's Chat, or one waiting in its Mailbox."""

        stream_id = required_resident_id(resident_id)
        target = str(message_id or "").strip()
        if not target:
            return None
        shown = self.agent_data.chat_message(stream_id, target)
        if shown is not None:
            return shown
        waiting = self.state_store.mailbox_entry(stream_id, target)
        stored = (waiting or {}).get("message") or {}
        if isinstance(stored.get("content_parts"), list):
            return stored
        return self.agent_data.group_source_message(stream_id, target)

    def waiting(self, resident_id: object, message_id: object) -> bool:
        """Whether the message waits in the Resident's Mailbox (accepted, not delivered)."""

        return self.state_store.mailbox_entry(required_resident_id(resident_id), str(message_id or "").strip()) is not None

    def message_business_ref(self, resident_id: object, message_id: object) -> str:
        """The business record a delivered Message belongs to; empty when none was given."""

        stored = self.stored_message(resident_id, message_id)
        return str((stored or {}).get("business_ref") or "").strip()

    def public_message(
        self,
        resident_id: object,
        message_id: object,
    ) -> dict[str, Any]:
        stored = self.message(resident_id, message_id)
        if stored is None:
            raise KeyError(str(message_id or ""))
        return stored

    def artifact(
        self,
        resident_id: object,
        artifact_id: object,
    ) -> dict[str, Any]:
        target = str(artifact_id or "").strip()
        suffix = ":provider_response"
        if not target.endswith(suffix):
            raise KeyError(target)
        message_id = target[:-len(suffix)]
        stored = self.stored_message(resident_id, message_id)
        response = stored.get("provider_response") if stored else None
        if not isinstance(response, Mapping):
            raise KeyError(target)
        return {
            "artifact_id": target,
            "media_type": "application/json",
            "value": copy.deepcopy(dict(response)),
        }

    def subagent_run(
        self,
        resident_id: object,
        trace_ref: object,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        """The steps of one subagent run of the Resident, from the run's own trace (its key ``trace_ref``)."""

        from management_common.provider_subagent_runs import trace_steps

        key = str(trace_ref or "").strip()
        if not key:
            raise KeyError(key)
        child_id = self.agent_data.subagent_id(required_resident_id(resident_id), key)
        sequence_id = self.agent_data.trace_sequence_id(child_id) if self.agent_data.agent(child_id) else ""
        if not sequence_id:
            raise KeyError(key)
        page = self.agent_data.read_sequence(sequence_id, offset=offset, limit=limit)
        return {
            "trace_ref": key,
            "total": int(page.get("total") or 0),
            "offset": int(page.get("offset") or 0),
            "updated_at": str(page.get("updated_at") or ""),
            "steps": trace_steps(page.get("segments") or [], offset=int(page.get("offset") or 0)),
        }

    def update_message(
        self,
        resident_id: object,
        message_id: object,
        *,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace a delivered message where it stands in the trace by a new version of it."""

        stream_id = required_resident_id(resident_id)
        target = str(message_id or "").strip()
        if not target:
            raise ValueError("Conversation message update requires message_id")
        with self.events.protocol_scope(stream_id):
            replacement = protocol_message(message or {})
            if required_message_id(replacement) != target:
                raise ValueError("Conversation message update identity changed")
            stored = self.agent_data.chat_message(stream_id, target) or {}
            # Display fields come from the new version; other private fields stay.
            fields = {**stored, **{key: message[key] for key in DISPLAY_FIELDS if message.get(key)}}
            persisted = self.agent_data.update_chat_message(
                stream_id,
                target,
                message_for_storage(replacement, private_fields=fields),
            )
            public = message_from_storage(persisted)
            event = self.state_store.database.write(
                lambda connection: self.events.record_event(
                    connection,
                    stream_id,
                    MESSAGE_UPDATED,
                    {"message": message_header(public)},
                    subject_id=target,
                )
            )
        return {**event, "payload": {"message": public}} if event else {}

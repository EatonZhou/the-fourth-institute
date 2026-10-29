"""Chat writes: delivering, updating and grouping the messages of an Agent's trace."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from management_common.json_columns import json_value

from .agent_data_chat import chat_payload, current_member, group_source_members, message_contents
from .agent_data_executions import refresh_final_reply
from .agent_data_messages import (
    answer_text,
    canonical_chat_message,
    chat_message_text,
    message_segment_type,
)
from .agent_data_segments import insert_segment, read_segment
from .agent_data_store import (
    canonical_json,
    chat_segment_id,
    new_identifier,
    stable_segment_id,
    utc_now,
)
from .agent_data_traces import (
    append_sequence_ids,
    ensure_agent_trace,
    locked_trace,
    place_operation,
    touch_sequence,
    trace_id,
)
from .agent_operations import next_position, put_operation
from .message_projection import message_facts, wrap_message
from . import chat_position_index


def _same_message(stored: Mapping[str, Any], requested: Mapping[str, Any]) -> None:
    if (
        chat_message_text(stored) != chat_message_text(requested)
        or str(stored.get("role") or "") != str(requested.get("role") or "")
        or str(stored.get("author_id") or "") != str(requested.get("author_id") or "")
    ):
        raise ValueError("Chat message identity refers to different immutable content")
    # How it was delivered (with a request, in a group) is the delivery's to say.
    ignored = {"activated_at", "segment_id", "released_with", "grouped_into_message_id", "business_ref"}
    if (
        {key: value for key, value in stored.items() if key not in ignored}
        != {key: value for key, value in requested.items() if key not in ignored}
    ):
        raise ValueError("Chat message metadata changed without an explicit update")


def _check_tool_source(connection: Any, agent_id: str, message: Mapping[str, Any]) -> None:
    """A tool or turn card shows a Segment of the Agent's own work, exactly as recorded."""

    tool_parts = [
        part for part in message["content_parts"]
        if part.get("type") in {"tool_call", "tool_result", "native_turn", "resource_changes", "subagent_run"}
        and part.get("segment_id")
    ]
    if message["role"] != "agent" or len(tool_parts) != 1:
        return
    part = tool_parts[0]
    source = (_operation_record if part["type"] in {"native_turn", "resource_changes", "subagent_run"} else read_segment)(connection, str(part["segment_id"]))
    expected_type = {
        "native_turn": "model", "resource_changes": "artifact", "subagent_run": "artifact",
    }.get(part.get("type"), "tool")
    if (
        source.get("segment_type") != expected_type
        or source.get("created_by_agent_id") != agent_id
        or source.get("content") != part
    ):
        raise ValueError("tool message must reference its own canonical Segment")


def _operation_record(connection: Any, operation_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT data_json FROM agent_operations WHERE operation_id=%s", (operation_id,)).fetchone()
    if row is None:
        raise KeyError(operation_id)
    return json_value(row["data_json"])


def _append_chat_message(
    connection: Any,
    agent_id: str,
    message: Mapping[str, Any],
    *,
    activated_at: str = "",
) -> tuple[dict[str, Any], bool]:
    """Put one message at the end of the Agent's trace; a message already there is returned as it is."""

    canonical = canonical_chat_message(message)
    message_id = str(canonical["message_id"])
    role = str(canonical["role"])
    actor_ref = str(canonical.get("author_id") or "")
    canonical["created_at"] = str(canonical.get("created_at") or utc_now())
    sequence = ensure_agent_trace(connection, agent_id, actor_ref=actor_ref)
    sequence_id = str(sequence["sequence_id"])
    # Appends of one message serialize on its trace.
    connection.execute("SELECT 1 FROM sequences WHERE sequence_id = %s FOR UPDATE", (sequence_id,))
    existing = current_member(connection, sequence_id, message_id)
    captured = None
    if existing is None and agent_id.startswith("resident_") and role == "agent" and canonical.get("execution_id"):
        candidates = connection.execute(
            "SELECT segment_id,metadata_json FROM segments WHERE execution_id=%s "
            "AND segment_type='model' AND metadata_json::jsonb #>> '{message,message_id}'=%s "
            "AND NOT (metadata_json::jsonb ? 'subagent') ORDER BY finished_at,segment_id",
            (canonical["execution_id"], message_id),
        ).fetchall()
        if candidates:
            contents = message_contents(connection, [row["segment_id"] for row in candidates])
            # Several captured Provider finals can share the canonical final
            # message ID. Deliver the selected content, never an arbitrary row.
            selected = answer_text(agent_id, canonical)
            captured = next((row for row in candidates if answer_text(agent_id, chat_payload(
                row["segment_id"], row["metadata_json"], contents[row["segment_id"]],
            )) == selected), None)
            if captured is None:
                raise ValueError("Captured message does not match the selected content")
    extras_id = str(existing["segment_id"]) if existing else (
        str(captured["segment_id"]) if captured else chat_segment_id(agent_id, message_id)
    )
    if canonical.get("provider_response") and captured is None:
        put_operation(connection, operation_id="message_artifact:" + extras_id, kind="message_artifact", agent_id=agent_id,
            created_at=canonical["created_at"], data={"provider_response": canonical["provider_response"]})
    if existing is not None:
        stored = chat_payload(existing["segment_id"], existing["metadata_json"], message_contents(connection, [existing["segment_id"]])[existing["segment_id"]])
        if role == "user" and chat_message_text(stored) != chat_message_text(canonical):
            transformed = connection.execute("SELECT 1 FROM segment_relations WHERE target_segment_id=%s LIMIT 1", (existing["segment_id"],)).fetchone()
            if not transformed:
                _same_message(stored, canonical)
        return stored, False
    consumed = group_source_members(connection, sequence_id, [message_id]) if role == "user" else []
    if consumed:
        # Grouping removed this occurrence from the trace, not history.
        # A delivery replay validates and reuses the accepted source; it
        # cannot rewrite it or resurrect it in the current Chat.
        source = consumed[-1]
        stored = chat_payload(source["segment_id"], source["metadata_json"],
                              message_contents(connection, [source["segment_id"]])[source["segment_id"]])
        _same_message(stored, canonical)
        return stored, False
    _check_tool_source(connection, agent_id, canonical)
    canonical["activated_at"] = str(activated_at or canonical.get("activated_at") or utc_now())
    if captured is not None:
        # The Execution collector saved immutable content, but did not
        # deliver it to this Resident's Chat. This consumer now places the
        # captured Segment in the trace, then its caller publishes it.
        segment_id = str(captured["segment_id"])
        display = {
            key: canonical[key]
            for key in ("content_parts", "origin", "usage", "activated_at", "provider_response")
            if key in canonical
        }
        put_operation(
            connection, operation_id="message_artifact:" + segment_id,
            kind="message_artifact", agent_id=agent_id,
            created_at=canonical["created_at"], data=display,
        )
        append_sequence_ids(
            connection, sequence=sequence, segment_ids=[segment_id], actor_ref=actor_ref,
        )
        stored = chat_payload(
            segment_id, captured["metadata_json"],
            message_contents(connection, [segment_id])[segment_id],
        )
        return {**stored, **display}, True
    segment_type = message_segment_type(role)
    fields = {
        "segment_type": segment_type,
        "content": wrap_message(chat_message_text(canonical)) if role == "user" else chat_message_text(canonical),
        "started_at": canonical["created_at"] if segment_type != "model" else "",
        "finished_at": canonical["created_at"],
        "status": "completed",
        "created_by_actor_ref": actor_ref,
        "performed_by_actor_ref": actor_ref if segment_type != "model" else "portal",
        "content_type": "text/plain; charset=utf-8",
        "metadata": {"role": role, "kind": "user_message" if role == "user" else "assistant_message" if role == "agent" else "portal_notice", "message": message_facts(canonical)},
        "created_by_agent_id": agent_id,
        "execution_id": str(canonical.get("execution_id") or ""),
    }
    segment_id = chat_segment_id(agent_id, message_id)
    parts = canonical["content_parts"]
    # Visible model text enters the trace here, through the Conversation
    # consumer. Structured notices without text remain operations.
    operational = (role == "system" and not canonical.get("harness_input")) or (role == "agent" and not chat_message_text(canonical)) or any(
        part.get("type") in {"native_turn", "resource_changes", "subagent_run"} for part in parts)
    if operational:
        put_operation(connection, operation_id=segment_id, kind="notice", agent_id=agent_id,
            created_at=canonical["created_at"], data=fields)
        place_operation(connection, sequence_id, segment_id, next_position(connection, sequence_id), actor_ref)
        return {**canonical, "segment_id": segment_id}, True
    insert_segment(connection, segment_id=segment_id, **fields)
    append_sequence_ids(connection, sequence=sequence, segment_ids=[segment_id], actor_ref=actor_ref)
    return {**canonical, "segment_id": segment_id}, True


class ChatWrites:
    """Writes of Chat messages and of the operation records they cite."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def display_chat_message(
        self,
        agent_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        projected, _changed = self.project_chat_message(agent_id, message)
        return projected

    def put_operation_record(self, *, segment_id: str, created_by_agent_id: str,
                             created_at: str = "", **fields: Any) -> str:
        """Store a UI artifact as an operational fact, outside Segments."""
        data = {**fields, "segment_id": segment_id, "created_by_agent_id": created_by_agent_id,
                "created_at": created_at or utc_now()}
        return self.database.write(lambda connection: put_operation(
            connection, operation_id=segment_id, kind="artifact", agent_id=created_by_agent_id,
            created_at=data["created_at"], data=data))

    def operation_record(self, operation_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return _operation_record(connection, operation_id)

    def project_chat_message(
        self,
        agent_id: str,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Append a message to the Agent's Chat; returns it and whether it was new."""

        def write(connection: Any) -> tuple[dict[str, Any], bool]:
            projected, changed = _append_chat_message(
                connection, agent_id, message, activated_at=activated_at,
            )
            if changed:
                refresh_final_reply(connection, str(projected.get("execution_id") or ""))
            return projected, changed

        return self.database.write(write)

    def merge_message_group(self, agent_id: str, message_ids: Sequence[str], target_id: str) -> None:
        """A composed message consumes its members in the current trace."""
        def write(connection: Any) -> None:
            sequence_id = locked_trace(connection, agent_id, "portal")
            consumed = {row["member_id"]: row for row in group_source_members(connection, sequence_id, message_ids)}
            sources = [current_member(connection, sequence_id, identity) or consumed.get(identity) for identity in message_ids]
            sources = [row for row in sources if row is not None and row["segment_id"] != target_id]
            target = connection.execute("SELECT ordinal FROM sequence_segments WHERE sequence_id=%s AND segment_id=%s", (sequence_id, target_id)).fetchone()
            if not sources or target is None:
                return
            at = utc_now()
            for source in sources:
                source_id = str(source["segment_id"])
                relation_id = stable_segment_id("group", source_id, target_id).replace("segment_", "relation_", 1)
                connection.execute("INSERT INTO segment_relations VALUES (%s,'merged_from',%s,%s,%s,'message.group','1',%s,'portal','{}') ON CONFLICT DO NOTHING",
                                   (relation_id, source_id, target_id, target_id, at))
            position = min([int(target["ordinal"]), *[int(row["ordinal"]) for row in sources if "ordinal" in row.keys()]])
            connection.execute("DELETE FROM sequence_segments WHERE sequence_id=%s AND segment_id=ANY(%s)",
                               (sequence_id, [target_id, *[row["segment_id"] for row in sources]]))
            connection.execute("INSERT INTO sequence_segments VALUES (%s,%s,%s)", (sequence_id, position, target_id))
            touch_sequence(connection, sequence_id, "portal")
            chat_position_index.refresh_sequence(connection, sequence_id)
        self.database.write(write)

    def update_chat_message(
        self,
        agent_id: str,
        message_id: str,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace a message where it stands in the trace by a new version of it."""

        def write(connection: Any) -> dict[str, Any]:
            sequence_id = trace_id(connection, agent_id)
            row = current_member(connection, sequence_id, message_id)
            if row is None:
                raise KeyError(message_id)
            previous = chat_payload(row["segment_id"], row["metadata_json"], message_contents(connection, [row["segment_id"]])[row["segment_id"]])
            canonical = canonical_chat_message(message)
            if str(canonical["message_id"]) != message_id:
                raise ValueError("Chat message update changed identity")
            source_segment_id = str(row["segment_id"])
            canonical["created_at"] = str(canonical.get("created_at") or utc_now())
            if not canonical.get("activated_at") and previous.get("activated_at"):
                canonical["activated_at"] = previous["activated_at"]
            content = chat_message_text(canonical)
            if canonical["role"] == "user":
                content = wrap_message(content)
            metadata = {"role": str(canonical["role"]), "kind": "user_message" if canonical["role"] == "user" else "assistant_message" if canonical["role"] == "agent" else "portal_notice", "message": message_facts(canonical)}
            digest = hashlib.sha256(
                (content + "\0" + canonical_json(canonical)).encode("utf-8")
            ).hexdigest()[:24]
            target_segment_id = stable_segment_id(
                "chat-transform", agent_id, message_id, digest
            )
            role = str(canonical["role"])
            actor_ref = str(canonical.get("author_id") or "")
            created_at = str(canonical["created_at"])
            if target_segment_id == source_segment_id:
                return {**canonical, "segment_id": source_segment_id}
            notice = connection.execute("SELECT 1 FROM agent_operations WHERE operation_id=%s AND kind='notice'", (source_segment_id,)).fetchone()
            if notice:
                fields = {"segment_type": message_segment_type(role), "content": content,
                          "metadata": metadata, "created_by_actor_ref": actor_ref,
                          "execution_id": str(canonical.get("execution_id") or "")}
                put_operation(connection, operation_id=target_segment_id, kind="notice", agent_id=agent_id,
                              created_at=created_at, data=fields)
                connection.execute("UPDATE operation_positions SET operation_id=%s WHERE sequence_id=%s AND operation_id=%s",
                                   (target_segment_id, sequence_id, source_segment_id))
                touch_sequence(connection, sequence_id, actor_ref)
                chat_position_index.refresh_sequence(connection, sequence_id)
                return {**canonical, "segment_id": target_segment_id}
            insert_segment(
                connection,
                segment_id=target_segment_id,
                segment_type=message_segment_type(role),
                content=content,
                started_at="",
                finished_at=created_at,
                status="completed",
                created_by_actor_ref=actor_ref,
                performed_by_actor_ref="portal",
                content_type="text/plain; charset=utf-8",
                metadata=metadata,
                created_by_agent_id=agent_id,
                execution_id=str(canonical.get("execution_id") or ""),
            )
            operation_id = new_identifier("operation")
            relation_id = hashlib.sha256(
                f"transformed_from\0{source_segment_id}\0{target_segment_id}\0{operation_id}".encode()
            ).hexdigest()[:32]
            connection.execute(
                "\n                INSERT INTO segment_relations(\n                    relation_id, relation_type, source_segment_id,\n                    target_segment_id, operation_id, operation_type,\n                    operation_version, created_at, actor_ref, metadata_json\n                ) VALUES (%s, 'transformed_from', %s, %s, %s, 'chat.message.update',\n                          '1', %s, %s, '{}')\n                ",
                (
                    f"relation_{relation_id}",
                    source_segment_id,
                    target_segment_id,
                    operation_id,
                    utc_now(),
                    actor_ref,
                ),
            )
            connection.execute("SELECT 1 FROM sequences WHERE sequence_id = %s FOR UPDATE", (sequence_id,))
            connection.execute(
                'UPDATE sequence_segments SET segment_id = %s WHERE sequence_id = %s AND segment_id = %s',
                (target_segment_id, sequence_id, source_segment_id),
            )
            touch_sequence(connection, sequence_id, actor_ref)
            chat_position_index.refresh_sequence(connection, sequence_id)
            return {**canonical, "segment_id": target_segment_id}

        return self.database.write(write)

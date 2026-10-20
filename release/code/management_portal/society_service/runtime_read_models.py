from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


CONTENT_FIELDS = (
    'message', 'actual_sent_prompt', 'execution_request', 'input_message',
    'raw_message', 'effective_message', 'fork_context_input',
)
POSTGRES_TRIGGERS = frozenset({
    'conversation_event_delivery_insert',
})


def execution_control_state(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a Job's content-free scheduling projection; queue items keep a preview."""

    control = copy.deepcopy(dict(value))
    for field in CONTENT_FIELDS:
        control.pop(field, None)
    queue = []
    for raw in value.get("queue") or []:
        if not isinstance(raw, Mapping):
            continue
        item = copy.deepcopy(dict(raw))
        for field in CONTENT_FIELDS:
            item.pop(field, None)
        input_message = raw.get("input_message")
        if isinstance(input_message, Mapping):
            text_parts: list[str] = []
            media_parts: list[dict[str, Any]] = []
            for part in input_message.get("content_parts") or []:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") == "text":
                    text_parts.append(str(part.get("text") or ""))
                elif part.get("type") in {"image", "file"}:
                    media_parts.append(copy.deepcopy(dict(part)))
            item["conversation_queue_preview"] = {
                "message_id": str(raw.get("user_message_id") or ""),
                "author_id": str(input_message.get("author_id") or ""),
                "enqueued_at": str(
                    raw.get("enqueued_at")
                    or input_message.get("created_at")
                    or ""
                ),
                "content_preview": "\n".join(text_parts).strip()[:500],
                "media_parts": media_parts,
            }
        queue.append(item)
    control["queue"] = queue
    return control


def create_runtime_read_models(connection: Any) -> None:
    _create_postgres_runtime_read_models(connection)


def _create_postgres_runtime_read_models(connection: Any) -> None:
    connection.execute(
        'CREATE TABLE conversation_event_delivery ('
        'sequence BIGSERIAL PRIMARY KEY, resident_id TEXT NOT NULL, cursor BIGINT NOT NULL, '
        'FOREIGN KEY (resident_id, cursor) REFERENCES conversation_events(resident_id, cursor) ON DELETE CASCADE)'
    )
    connection.execute(
        'CREATE INDEX conversation_event_delivery_event_idx ON conversation_event_delivery(resident_id, cursor)'
    )
    connection.execute(
        """
        CREATE FUNCTION lineage_enqueue_conversation_event() RETURNS trigger AS $$
        BEGIN
            INSERT INTO conversation_event_delivery(resident_id, cursor)
            VALUES (NEW.resident_id, NEW.cursor);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    connection.execute(
        'CREATE TRIGGER conversation_event_delivery_insert AFTER INSERT ON conversation_events '
        'FOR EACH ROW EXECUTE FUNCTION lineage_enqueue_conversation_event()'
    )

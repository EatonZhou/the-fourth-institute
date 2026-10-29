from __future__ import annotations

import hashlib
from typing import Any, Mapping

from management_common.principal import message_origin

from . import agent_data_segments, agent_data_traces
from .agent_data_store import AgentDataStore, canonical_json, stable_segment_id
from .contracts import ExecutionInputControl, InputMedia
from .agent_data_input import input_message


def materialize_delivered_input(
    store: AgentDataStore, receipt: Mapping[str, Any], *, source_segment_id: str = "",
) -> dict[str, str] | None:
    control = ExecutionInputControl.from_payload(dict(receipt))
    if receipt.get("status") != "delivered" or not control.expected_turn_id:
        raise ValueError("Appended input requires a confirmed Provider delivery")
    if control.input[0].get("type") in {"user_input_response", "subagent_stop"}:
        return None
    segment_id = stable_segment_id("execution-appended-input", control.control_id)
    if source_segment_id and control.source_segment_id and source_segment_id not in {control.source_segment_id, segment_id}:
        raise ValueError("Appended input source does not match its receipt")
    source_id = source_segment_id or control.source_segment_id
    delivered_at = str(receipt.get("updated_at") or "")
    if not delivered_at:
        raise ValueError("Appended input receipt has no delivery timestamp")
    text = "\n\n".join(str(part["text"]) for part in control.input if part["type"] == "text")
    media = [
        {"media_type": "image", "source_type": "local_path" if part["type"] == "localImage" else "url",
         "source": part.get("path") or part.get("url")}
        for part in control.input if part["type"] in {"localImage", "image"}
    ]

    def write(connection):
        execution = connection.execute(
            'SELECT * FROM executions WHERE execution_id=%s', (control.execution_id,),
        ).fetchone()
        if execution is None:
            return None
        if execution["provider_turn_id"] != control.expected_turn_id or not execution["provider_session_ref"]:
            raise ValueError("Appended input receipt does not match its Provider execution")
        agent_id = str(execution["agent_id"])
        trace = agent_data_traces.agent_trace(connection, agent_id)
        if trace is None:
            raise ValueError("Appended input execution has no Agent Trace")
        trace_id = str(trace["sequence_id"])

        def ordinal(identity: str) -> int | None:
            row = connection.execute(
                'SELECT ordinal FROM sequence_segments WHERE sequence_id=%s AND segment_id=%s',
                (trace_id, identity),
            ).fetchone() if identity else None
            return int(row["ordinal"]) if row else None

        if ordinal(segment_id) is not None:
            return {"segment_id": segment_id, "provider_turn_id": control.expected_turn_id}
        source_ordinal = ordinal(source_id)
        if source_id and source_ordinal is None:
            raise ValueError("Appended input source is outside the Agent Trace")
        linked_source = source_id if source_ordinal is not None else ""
        content = input_message(text, [InputMedia(**item) for item in media]) if media else text
        agent_data_segments.insert_segment(
            connection, segment_id=segment_id, segment_type="input", content=content,
            started_at=delivered_at, finished_at=delivered_at,
            status="completed", created_by_actor_ref=control.actor, performed_by_actor_ref=control.actor,
            content_type="application/json" if media else "text/plain; charset=utf-8", created_by_agent_id=agent_id,
            execution_id=control.execution_id, metadata={
                **(agent_data_segments.read_segment(connection, linked_source)["metadata"] if linked_source else {}),
                "role": "user", "kind": "appended_provider_input", "control_id": control.control_id,
                "provider_turn_id": control.expected_turn_id,
                "input_media": media,
                "origin": message_origin(control.actor),
            },
        )
        if linked_source:
            relation_id = "relation_" + hashlib.sha256(
                f"derived_from\0{source_id}\0{segment_id}\0{control.control_id}".encode()
            ).hexdigest()[:32]
            connection.execute(
                'INSERT INTO segment_relations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (relation_id, "transformed_from", source_id, segment_id, control.control_id,
                 "provider_input.append", "1", delivered_at, control.actor, canonical_json({})),
            )
        # The received form replaces its source occurrence at the same position.
        if ordinal(segment_id) is None:
            if source_ordinal is not None:
                connection.execute("UPDATE sequence_segments SET segment_id=%s WHERE sequence_id=%s AND segment_id=%s", (segment_id, trace_id, source_id))
                agent_data_traces.touch_sequence(connection, trace_id, control.actor)
                from . import chat_position_index
                chat_position_index.refresh_sequence(connection, trace_id)
            else:
                agent_data_traces.append_sequence_ids(
                    connection, sequence=trace, segment_ids=[segment_id], actor_ref=control.actor,
                )
        return {"segment_id": segment_id, "provider_turn_id": control.expected_turn_id}

    return store.database.write(write)

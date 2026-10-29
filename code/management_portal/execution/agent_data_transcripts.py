"""A Provider session's transcript and the prompts its Provider received."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from management_common.database_time import iso_time

from .agent_data_chat import ChatReads
from .agent_data_contexts import attach_provider_turns, session_transcript_ids
from .agent_data_segments import read_segments
from .agent_data_store import canonical_json


def _timeline_segment(
    segment: Mapping[str, Any],
    *,
    turn_id: str = "",
) -> dict[str, Any]:
    metadata = dict(segment.get("metadata") or {})
    content = segment.get("content")
    source = str(metadata.get("source") or "") or {
        "input": "human_prompt",
        "model": "agent_output",
        "harness": "harness_output",
        "compaction": "context_compaction",
    }.get(str(segment.get("segment_type") or ""), "")
    return {
        "id": str(segment.get("segment_id") or ""),
        "segment_id": str(segment.get("segment_id") or ""),
        "created_at": str(segment.get("finished_at") or segment.get("started_at") or ""),
        "started_at": str(segment.get("started_at") or ""),
        "finished_at": str(segment.get("finished_at") or ""),
        "source": source,
        "kind": str(metadata.get("kind") or "message"),
        "title": str(metadata.get("title") or ""),
        "content": (
            str(content or "")
            if not isinstance(content, Mapping)
            else canonical_json(content)
        ),
        "content_length": len(str(content or "")),
        "line_count": str(content or "").count("\n") + 1,
        "role": str(metadata.get("role") or ""),
        "author": str(
            metadata.get("author") or segment.get("created_by_actor_ref") or ""
        ),
        "turn_id": str(turn_id or ""),
        "execution_id": str(segment.get("execution_id") or ""),
    }


def _tool_resource(
    segment: Mapping[str, Any],
    *,
    turn_id: str = "",
) -> dict[str, Any]:
    content = (
        segment.get("content")
        if isinstance(segment.get("content"), Mapping)
        else {}
    )
    metadata = dict(segment.get("metadata") or {})
    return {
        "id": str(content.get("call_id") or segment.get("segment_id") or ""),
        "segment_id": str(segment.get("segment_id") or ""),
        "kind": "tool",
        "tool_name": str(content.get("tool_name") or metadata.get("tool_name") or ""),
        "server": str(
            content.get("tool_namespace") or metadata.get("tool_namespace") or ""
        ),
        "namespace": str(content.get("tool_namespace") or ""),
        "call_id": str(content.get("call_id") or ""),
        "created_at": str(segment.get("finished_at") or segment.get("started_at") or ""),
        "started_at": str(segment.get("started_at") or ""),
        "completed_at": str(segment.get("finished_at") or ""),
        "status": str(segment.get("status") or ""),
        "turn_id": str(turn_id or ""),
        "input": content.get("input"),
        "result": content.get("result"),
        "error": content.get("error"),
        "external_effect_refs": list(content.get("external_effect_refs") or []),
    }


def _sessions_by_backend(
    connection: Any, provider: str, backend_session_id: str, backend_route_id: str = "",
) -> list[Any]:
    if backend_route_id:
        return connection.execute(
            'SELECT * FROM provider_sessions WHERE provider = %s AND backend_route_id = %s '
            'AND backend_session_id = %s',
            (provider, backend_route_id, backend_session_id),
        ).fetchall()
    return connection.execute(
        'SELECT * FROM provider_sessions WHERE provider = %s AND backend_session_id = %s '
        'ORDER BY updated_at DESC',
        (provider, backend_session_id),
    ).fetchall()


class SessionTranscripts:
    """Reads of what a Provider session was given and answered."""

    def __init__(self, database: Any, *, chat: ChatReads) -> None:
        self.database = database
        self.chat = chat

    def session_transcript(
        self,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str = "",
    ) -> dict[str, Any] | None:
        """A Provider session's transcript, read from its Agent trace, as timeline items."""

        with self.database.read() as connection:
            sessions = _sessions_by_backend(connection, provider, backend_session_id, backend_route_id)
            if not sessions:
                return None
            session = sessions[0]
            session_ref = str(session["provider_session_ref"])
            segments = read_segments(
                connection, session_transcript_ids(connection, session_ref),
            )
            attach_provider_turns(connection, segments)
            return {
                "provider_session_ref": session_ref,
                "provider": str(session["provider"]),
                "backend_route_id": str(session["backend_route_id"]),
                "backend_session_id": str(session["backend_session_id"]),
                "updated_at": (iso_time(session["updated_at"]) or ""),
                "items": [
                    _timeline_segment(
                        segment,
                        turn_id=segment["provider_turn_id"],
                    )
                    for segment in segments
                    if str(segment.get("segment_type")) != "tool"
                ],
                "resource_events": [
                    _tool_resource(
                        segment,
                        turn_id=segment["provider_turn_id"],
                    )
                    for segment in segments
                    if str(segment.get("segment_type")) == "tool"
                ],
                "embedded_images": [],
            }

    def provider_user_prompts(
        self,
        *,
        provider: str,
        backend_session_id: str,
    ) -> list[dict[str, Any]]:
        transcript = self.session_transcript(
            provider=provider,
            backend_session_id=backend_session_id,
        )
        if transcript is None:
            return []
        return [
            item
            for item in transcript.get("items") or []
            if str(item.get("role") or "") in {"user", "human"}
            or str(item.get("source") or "") == "human_prompt"
        ]

    def message_provider_prompts(self, agent_id: str, message_id: str) -> list[dict[str, Any]]:
        """The user prompts of the Provider session whose execution took this message.

        A ``provider_api`` Resident runs each input in a session of its own,
        and a context reset starts a new session, so the Resident's current
        session may not be the one that took an earlier message.
        """

        try:
            source = self.chat.chat_message_provenance(agent_id, message_id)
        except KeyError:
            return []
        if not source["provider_session_ref"]:
            return []
        with self.database.read() as connection:
            session = connection.execute(
                "SELECT provider, backend_session_id FROM provider_sessions WHERE provider_session_ref = %s",
                (source["provider_session_ref"],),
            ).fetchone()
        if session is None:
            return []
        return self.provider_user_prompts(
            provider=str(session["provider"]), backend_session_id=str(session["backend_session_id"]),
        )

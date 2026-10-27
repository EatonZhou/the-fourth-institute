"""Resident domain responsibilities: actual sent."""
from __future__ import annotations

from . import resident_backend, resident_conversation_stream
from typing import Any


def read_actual_sent_prompt(
    resident_id: str,
    actor: str,
    message_id: str,
) -> dict[str, Any]:
    from .domain_ports import execution_port
    from . import resident_conversation_projection_domain, resident_record_domain
    if not resident_conversation_projection_domain.can_view_actual_sent_prompts(actor, resident_id):
        raise PermissionError("无权读取实际发送内容")
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    stored = resident_conversation_stream.get_repository().message(
        target.resident_id,
        message_id,
    )
    if stored is None or str(stored.get("role") or "").casefold() not in {"user", "human"}:
        raise KeyError(message_id)
    candidate = dict(stored)
    backend = resident_backend.execution_backend(target.record)
    session_id = resident_backend.record_session_id(target.record)
    resident_conversation_projection_domain.attach_actual_sent_prompts(
        [candidate],
        session_ids=[session_id] if session_id else [],
        provider=resident_backend.execution_provider({**target.record, "backend": backend}),
    )
    if not candidate.get("actual_sent_prompt"):
        # The session whose execution took the message: a provider_api Resident
        # runs each input in a session of its own, and a context reset starts a new one.
        resident_conversation_projection_domain.attach_actual_sent_prompts([candidate], candidates=resident_conversation_projection_domain.actual_sent_prompt_candidates(
            execution_port.message_provider_prompts(target.resident_id, str(message_id or ""))))
    return {
        "resident_id": target.resident_id,
        "message_id": str(message_id or ""),
        "content": str(candidate.get("actual_sent_prompt") or ""),
        "source": str(candidate.get("actual_sent_prompt_source") or ""),
    }

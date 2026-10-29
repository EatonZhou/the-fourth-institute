"""The state the AgentData projection keeps for one running execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import ExecutionRequest
from .session_manager import RuntimeSession


def input_message_id(request: ExecutionRequest) -> str:
    # Resident API requests identify their input in metadata; native sessions
    # also pass it as a client option for the provider transport. Both refer
    # to the same Portal input occurrence, independently of its current text.
    return str(request.metadata.get("user_message_id")
               or request.runtime_options.get("client_user_message_id") or "").strip()


@dataclass
class ExecutionProjection:
    request: ExecutionRequest
    session: RuntimeSession
    agent_id: str
    input_segment_id: str
    effective_input_segment_id: str
    system_segment_ids: list[str] = field(default_factory=list)
    provider_session_ref: str = ""
    provider: str = ""
    backend_session_id: str = ""
    backend_route_id: str = ""
    turn_id: str = ""
    final_segment_id: str = ""
    compaction_segment_id: str = ""
    persisted_segments: set[str] = field(default_factory=set)
    pending_compactions: list[tuple[dict[str, Any], str, dict[str, str]]] = field(
        default_factory=list
    )
    provider_input_observed: bool = False
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    trace_key: str = ""
    subagent: dict[str, str] = field(default_factory=dict)
    children: dict[str, ExecutionProjection] = field(default_factory=dict)

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import (
    ExecutionRequest,
    InputMedia,
    SessionOptions,
    SystemInstructions,
)




def build_agent_session_request(
    *,
    actor: str,
    prompt: str,
    provider: str,
    operation: str,
    correlation_id: str,
    cwd: str,
    permission_profile: str = "default",
    runtime_options: Mapping[str, object] | None = None,
    binding_id: str = "",
    backend_route_id: str = "",
    context_source_binding_id: str = "",
    input_media: Sequence[InputMedia] = (),
    system_instructions: SystemInstructions | None = None,
    metadata: Mapping[str, object] | None = None,
    agent_id: str = "",
    priority: int = 0,
) -> ExecutionRequest:
    """Build the canonical Agent Session operation request."""

    clean_binding_id = str(binding_id or "").strip()
    clean_session_mode = "persistent" if clean_binding_id else "stateless"
    return ExecutionRequest.new(
        actor=str(actor or "").strip(),
        prompt=str(prompt or ""),
        service_type="agent_session",
        provider=str(provider or ""),
        operation=str(operation or "turn").strip(),
        agent_id=str(agent_id or clean_binding_id).strip(),
        priority=int(priority),
        correlation_id=str(correlation_id or ""),
        cwd=str(cwd or ""),
        permission_profile=str(permission_profile or "default"),
        input_media=tuple(input_media),
        runtime_options=dict(runtime_options or {}),
        session=SessionOptions(
            mode=clean_session_mode,
            binding_id=clean_binding_id,
            backend_route_id=str(backend_route_id or "").strip(),
            context_source_binding_id=str(
                context_source_binding_id or ""
            ).strip(),
        ),
        system_instructions=system_instructions or SystemInstructions(),
        metadata=dict(metadata or {}),
    )


def build_agent_turn_request(
    *,
    actor: str,
    prompt: str,
    provider: str,
    correlation_id: str,
    cwd: str,
    permission_profile: str = "default",
    runtime_options: Mapping[str, object] | None = None,
    binding_id: str = "",
    backend_route_id: str = "",
    input_media: Sequence[InputMedia] = (),
    system_instructions: SystemInstructions | None = None,
    metadata: Mapping[str, object] | None = None,
    agent_id: str = "",
) -> ExecutionRequest:
    return build_agent_session_request(
        actor=actor,
        prompt=prompt,
        provider=provider,
        operation="turn",
        correlation_id=correlation_id,
        cwd=cwd,
        permission_profile=permission_profile,
        runtime_options=runtime_options,
        binding_id=binding_id,
        backend_route_id=backend_route_id,
        input_media=input_media,
        system_instructions=system_instructions,
        metadata=metadata,
        agent_id=agent_id,
    )


def build_chat_completion_request(
    *,
    actor: str,
    prompt: str,
    provider: str,
    correlation_id: str = "",
    runtime_options: Mapping[str, object] | None = None,
    system_instructions: SystemInstructions | None = None,
    metadata: Mapping[str, object] | None = None,
    agent_id: str = "",
) -> ExecutionRequest:
    """Build the canonical stateless Chat Completion request."""

    return ExecutionRequest.new(
        actor=str(actor or "").strip(),
        prompt=str(prompt or "Run chat completion."),
        service_type="chat_completion",
        provider=str(provider or ""),
        operation="generate",
        agent_id=str(agent_id or "").strip(),
        correlation_id=str(correlation_id or ""),
        permission_profile="provider_api",
        runtime_options=dict(runtime_options or {}),
        session=SessionOptions(mode="stateless"),
        system_instructions=system_instructions or SystemInstructions(),
        metadata=dict(metadata or {}),
    )

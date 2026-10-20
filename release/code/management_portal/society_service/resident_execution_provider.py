from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol

from management_portal.execution.provider_catalog import AGENT_PROVIDERS, provider_name

from .domain_ports import resident_lineage_host_port


@dataclass(frozen=True)
class ExecutionCapabilities:
    synchronous_delivery: bool = False
    supports_context: bool = True
    supports_media: bool = True
    supports_skills: bool = True
    supports_fork: bool = True
    supports_session_restart: bool = True
    supports_active_turn_input: bool = False
    native_chat: bool = True
    local_tools: bool = True
    workspace_write: bool = True
    native_permissions: bool = True
    runtime_settings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeliveryRequest:
    lineage_id: str
    resident_id: str
    actor_id: str
    message: str
    resident: Mapping[str, Any] = field(default_factory=dict)
    delivery: Mapping[str, Any] = field(default_factory=dict)
    persist_messages: bool = True


@dataclass(frozen=True)
class DeliveryResult:
    reply: str = ""
    assistant_message: Mapping[str, Any] | None = None
    resident_updates: Mapping[str, Any] = field(default_factory=dict)


# Where a Resident's initial instructions (its resident.md) travel:
# ``user_prompt`` - in front of the turn's user message, which stays in the
#   Provider session's history (Codex, DSH);
# ``system_instructions`` - with Portal's system instructions, sent by the same
#   policy and placed by the Provider adapter (Claude Code, whose system prompt is
#   given at every process start, Kimi Code, Provider API);
# ``none`` - a Resident without a model session.
INITIAL_INSTRUCTION_TRANSPORTS = frozenset({"user_prompt", "system_instructions", "none"})


class ExecutionProvider(Protocol):
    backend: str
    mode: str
    capabilities: ExecutionCapabilities
    initial_instructions_transport: str

    def deliver(self, request: DeliveryRequest) -> DeliveryResult: ...


SessionDelivery = Callable[[DeliveryRequest], DeliveryResult]
_session_delivery: SessionDelivery | None = None


def bind_session_delivery(delivery: SessionDelivery) -> None:
    if not callable(delivery):
        raise TypeError("Session delivery implementation must be callable")
    global _session_delivery
    _session_delivery = delivery


class SessionExecutionProvider:
    mode = "new_session"

    def __init__(
        self,
        backend: str,
        capabilities: ExecutionCapabilities,
        *,
        initial_instructions_transport: str = "system_instructions",
    ) -> None:
        if initial_instructions_transport not in INITIAL_INSTRUCTION_TRANSPORTS - {"none"}:
            raise ValueError(
                "Unsupported initial instruction transport: "
                f"{initial_instructions_transport}"
            )
        self.backend = backend
        self.capabilities = capabilities
        self.initial_instructions_transport = initial_instructions_transport

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        delivery = _session_delivery
        if delivery is None:
            raise RuntimeError("Resident session delivery port is not configured")
        return delivery(request)


class ProviderApiExecutionProvider(SessionExecutionProvider):
    def __init__(self) -> None:
        super().__init__(
            "provider_api",
            ExecutionCapabilities(
                runtime_settings=("model", "prompt_context", "backend_options", "prompt_injection", "agent_harness"),
                supports_media=False,
                supports_fork=True,
                supports_session_restart=False,
                native_chat=False,
                local_tools=False,
                workspace_write=False,
                native_permissions=False,
            ),
        )


class DeterministicExecutionProvider:
    backend = "portal"
    mode = "deterministic"
    initial_instructions_transport = "none"
    capabilities = ExecutionCapabilities(
        synchronous_delivery=True,
        supports_context=False,
        supports_media=False,
        supports_skills=False,
        supports_fork=False,
        supports_session_restart=False,
        native_chat=False,
        local_tools=False,
        workspace_write=False,
        native_permissions=False,
    )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        result = resident_lineage_host_port.handle_deterministic_resident_message(
            request.lineage_id,
            request.resident_id,
            request.message,
            request.actor_id,
        )
        reply = str(
            result.get("reply") if isinstance(result, Mapping) else result
        ).strip()
        if not reply:
            raise RuntimeError("Deterministic Resident did not produce a reply")
        return DeliveryResult(reply=reply)


# A Fork receives the source's effective context from AgentData, so any agent
# backend that can open a new session with that context (an explicit create
# operation, or on its first turn) supports Forks; no native fork is needed.
_PROVIDERS: dict[str, ExecutionProvider] = {
    **{p.provider: SessionExecutionProvider(p.provider, ExecutionCapabilities(
        supports_media=p.media, supports_fork="create" in p.operations or p.resume_without_session,
        supports_active_turn_input=p.active_turn_input, runtime_settings=p.runtime_settings,
    ), initial_instructions_transport=p.initial_instructions_transport) for p in AGENT_PROVIDERS.values()},
    "provider_api": ProviderApiExecutionProvider(),
    "portal": DeterministicExecutionProvider(),
}

def _provider_for_key(
    backend: str,
    mode: str | None = None,
) -> ExecutionProvider:
    provider = _PROVIDERS.get(backend)
    if provider is None or (mode is not None and provider.mode != mode):
        raise ValueError(
            f"Unsupported Resident execution provider: {backend}"
            + (f"/{mode}" if mode is not None else "")
        )
    return provider


def initial_instructions_transport_for(backend: object) -> str:
    """Instruction transport is a Provider contract, independent of its account or live Session."""
    return _provider_for_key(provider_name(backend), None).initial_instructions_transport


def provider_for(record: Mapping[str, Any]) -> ExecutionProvider:
    backend = provider_name(record.get("backend"))
    # The canonical Resident stores its backend. Execution mode belongs to that
    # provider, not a second persisted routing key; explicit modes are validated.
    mode = str(record.get("mode") or "").strip().lower() or None
    provider = _provider_for_key(backend, mode)
    definition = AGENT_PROVIDERS.get(backend)
    if definition and definition.media and definition.catalog_module:
        from . import resident_backend
        try:
            route = (resident_backend.execution_account(record)["backend_route_id"]
                     if record.get("resident_id") else
                     record.get("backend_route_id") or record.get("provider_profile") or definition.default_route)
            media = bool(definition.catalog(route).get("capabilities", {}).get("media", definition.media))
        except (ValueError, RuntimeError):
            media = False
        return SessionExecutionProvider(
            backend, replace(provider.capabilities, supports_media=media),
            initial_instructions_transport=provider.initial_instructions_transport,
        )
    return provider


def validate_registration(backend: object, mode: object) -> ExecutionProvider:
    normalized_backend = provider_name(backend)
    normalized_mode = str(mode or "new_session").strip().lower()
    return _provider_for_key(
        normalized_backend,
        normalized_mode,
    )
def capability_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    provider = provider_for(record)
    capabilities = provider.capabilities
    definition = AGENT_PROVIDERS.get(provider.backend)
    runtime_capabilities = definition.runtime_capabilities() if definition else None
    if provider.backend == "provider_api":
        from management_portal.execution.resident_configuration import api_capabilities
        runtime_capabilities = api_capabilities()
    return {
        "backend": provider.backend,
        "native_chat": capabilities.native_chat,
        "portal_managed_transcript": True,
        "local_tools": capabilities.local_tools,
        "workspace_write": capabilities.workspace_write,
        "native_fork": capabilities.supports_fork,
        "portal_fork": capabilities.supports_fork,
        "native_permissions": capabilities.native_permissions,
        "image_input": capabilities.supports_media,
        "context_input": capabilities.supports_context,
        "session_restart": capabilities.supports_session_restart,
        "active_turn_input": capabilities.supports_active_turn_input,
        "synchronous_delivery": capabilities.synchronous_delivery,
        "runtime_settings": list(capabilities.runtime_settings),
        "runtime_capabilities": runtime_capabilities,
        **(
            {"deterministic": True}
            if capabilities.synchronous_delivery
            else {}
        ),
    }

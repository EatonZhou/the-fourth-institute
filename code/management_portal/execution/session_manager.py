from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .agent_data_store import clean_string_tuple, AgentDataStore, get_agent_data_store, utc_now
from .contracts import ExecutionRequest


@dataclass(frozen=True)
class RuntimeSession:
    mode: str
    binding_id: str
    backend_session_id: str
    is_new_backend_session: bool
    backend_route_id: str = ""
    context_fingerprint: str = ""
    context_injected_at: str = ""
    context_injection_count: int = 0
    harness_tool_names: tuple[str, ...] = ()
    provider_session_ref: str = ""




class SessionManager:
    """Resolve Provider Session continuity from the canonical Agent store."""

    def __init__(
        self,
        *,
        agent_data: AgentDataStore | None = None,
    ) -> None:
        self.agent_data = agent_data or get_agent_data_store()

    def provider_binding(
        self, binding_id: str, *, expected_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = self.agent_data.provider_binding(binding_id)
        if expected_binding is not None:
            if not isinstance(expected_binding, Mapping):
                raise ValueError("Expected session binding must be an object")
            identity = ("provider_session_ref", "provider", "backend_session_id", "backend_route_id")
            if binding and any(binding.get(key) != expected_binding.get(key) for key in identity):
                raise RuntimeError("Provider binding changed before operation")
        return binding


    def provider_binding_routes(
        self, binding_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(binding_ids, Iterable) or isinstance(binding_ids, (str, bytes, Mapping)):
            raise ValueError("Session bindings must be a collection of identities")
        return self.agent_data.provider_binding_routes(binding_ids)

    def has_accepted_input(self, binding_id: str, *, agent_id: str) -> bool:
        return self.agent_data.binding_has_accepted_input(binding_id, agent_id=agent_id)


    def bind_session(
        self,
        binding_id: str,
        *,
        provider: str,
        backend_session_id: str,
        backend_route_id: str = "",
        context_fingerprint: str | None = None,
        context_injected_at: str | None = None,
        context_injection_count: int | None = None,
        harness_tool_names: Iterable[object] | None = None,
    ) -> dict[str, Any]:
        context = {
            name: value for name, value in {
                "context_fingerprint": context_fingerprint,
                "context_injected_at": context_injected_at,
                "context_injection_count": context_injection_count,
                "harness_tool_names": harness_tool_names,
            }.items() if value is not None
        }

        def bind(connection: Any) -> dict[str, Any]:
            provider_session = self.agent_data.ensure_provider_session(
                provider=provider,
                backend_route_id=backend_route_id,
                backend_session_id=backend_session_id,
                agent_id="",
                actor_ref="",
            )
            return self.agent_data.bind_provider_session(
                binding_id, str(provider_session["provider_session_ref"]), **context,
            )

        return self.agent_data.database.write(bind)

    def context_compacted_at(self, session: RuntimeSession, *, agent_id: str) -> str:
        """When the compaction heading the session's current context was made.

        Empty when the context has not been compacted.  Every Provider whose
        compaction AgentData records (Portal's own, and Codex, Claude Code and
        Kimi Code compacting internally) puts it at the head of the context.
        """
        if not session.provider_session_ref:
            return ""
        head = self.agent_data.session_context_head(session.provider_session_ref, agent_id=agent_id)
        return str(head["finished_at"] or "") if head else ""

    def replace_unaccepted_session(
        self, request: ExecutionRequest, session: RuntimeSession, *,
        backend_session_id: str, backend_route_id: str,
    ) -> dict[str, Any]:
        """Replace a lost empty provider session before its first input arrives.

        The caller establishes that the provider lost the thread. The binding
        still has to name the prepared session and must have accepted no input;
        another job's replacement or an existing conversation cannot be reset.
        """
        if session.mode != "persistent" or not session.provider_session_ref:
            raise ValueError("Empty session recovery requires an existing binding")
        if backend_route_id != session.backend_route_id:
            raise ValueError("Empty session recovery cannot change provider route")

        def write(connection: Any) -> dict[str, Any]:
            row = connection.execute(
                "SELECT provider_session_ref FROM provider_bindings WHERE binding_id=%s FOR UPDATE",
                (session.binding_id,),
            ).fetchone()
            if row is None or row["provider_session_ref"] != session.provider_session_ref:
                raise RuntimeError("Provider binding changed before empty session recovery")
            if self.has_accepted_input(session.binding_id, agent_id=request.agent_id):
                raise RuntimeError("Cannot replace a provider session that accepted input")
            prepared = f"recovery:{request.execution_id}"
            self.bind_session(
                prepared, provider=request.provider, backend_route_id=backend_route_id,
                backend_session_id=backend_session_id,

            )
            return self.replace_session(session.binding_id, prepared, expected_binding={
                "provider_session_ref": session.provider_session_ref,
                "provider": request.provider, "backend_session_id": session.backend_session_id,
            })

        return self.agent_data.database.write(write)

    def replace_session(
        self,
        binding_id: str,
        prepared_binding_id: str,
        *,
        expected_binding: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if expected_binding is not None and not isinstance(expected_binding, Mapping):
            raise ValueError("Expected session binding must be an object")
        expected = expected_binding or {}
        return self.agent_data.replace_provider_binding(
            binding_id,
            prepared_binding_id,
            expected_provider=str(expected.get("provider") or ""),
            expected_backend_session_id=str(expected.get("backend_session_id") or ""),
            expected_provider_session_ref=str(expected.get("provider_session_ref") or ""),
            expected_absent=expected_binding is not None and not expected_binding,
        )

    def restore_session(
        self, binding_id: str, replacement: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(replacement, Mapping):
            raise ValueError("Session replacement must be an object")
        current = replacement.get("current_binding")
        previous = replacement.get("previous_binding")
        if not isinstance(current, Mapping) or not isinstance(previous, Mapping):
            raise ValueError("Provider binding replacement state is incomplete")
        if current.get("binding_id") != binding_id or (previous and previous.get("binding_id") != binding_id):
            raise ValueError("Provider binding replacement belongs to another identity")
        if not previous:
            current_ref = str(current.get("provider_session_ref") or "")
            if not current_ref:
                raise ValueError("Provider binding restoration requires explicit current state")
            self.agent_data.delete_provider_binding(binding_id, expected_provider_session_ref=current_ref)
            return {}
        return self.agent_data.restore_provider_binding(
            binding_id,
            expected_provider_session_ref=str(current.get("provider_session_ref") or ""),
            binding=previous,
            expected_absent=current.get("detached") is True,
        )

    def release_session(
        self, binding_id: str, *, expected_provider_session_ref: str = "",
    ) -> None:
        self.agent_data.delete_provider_binding(
            binding_id, expected_provider_session_ref=expected_provider_session_ref,
        )

    def prepare(
        self,
        request: ExecutionRequest,
        *,
        recovery: bool = False,
    ) -> RuntimeSession:
        options = request.session.validate()
        backend_session_id = ""
        backend_route_id = str(options.backend_route_id or "")
        session_context: dict[str, Any] = {}
        destination_exists = False
        if options.mode == "persistent":
            destination = self.provider_binding(options.binding_id)
            destination_exists = bool(destination)
            binding_kind, separator, binding_owner_id = str(
                options.binding_id or ""
            ).partition(":")
            binding_agent_id = (
                binding_owner_id
                if binding_kind == "resident" and separator and binding_owner_id
                else str(destination.get("agent_id") or "")
            )
            request_agent_id = str(request.agent_id or "")
            if (
                binding_kind == "resident"
                and request_agent_id == str(options.binding_id or "")
            ):
                request_agent_id = binding_owner_id
            if (
                destination
                and request_agent_id
                and binding_agent_id
                and request_agent_id != binding_agent_id
            ):
                raise ValueError("Agent session belongs to another agent")
            if destination and destination["provider"] != request.provider:
                raise ValueError("Agent session belongs to another provider")
            if (
                request.operation in {"create", "fork"}
                and destination
                and not recovery
            ):
                raise ValueError(
                    f"Agent session already exists: {options.binding_id}"
                )
            if request.operation in {"compact", "release"} and not destination:
                raise KeyError(
                    f"Agent session not found: {options.binding_id}"
                )
            if destination:
                backend_session_id = str(
                    destination["backend_session_id"] or ""
                )
                backend_route_id = str(
                    destination.get("backend_route_id") or ""
                ) or backend_route_id
                session_context = destination
        if options.context_source_binding_id and not destination_exists:
            source_binding = self.provider_binding(
                options.context_source_binding_id,
                expected_binding=request.metadata.get("expected_source_binding"),
            )
            if not source_binding:
                raise KeyError(
                    "execution context source binding not found: "
                    f"{options.context_source_binding_id}"
                )
            if source_binding["provider"] != request.provider:
                raise ValueError(
                    "execution context source belongs to another provider"
                )
            backend_session_id = str(
                source_binding["backend_session_id"] or ""
            )
            source_route_id = str(
                source_binding.get("backend_route_id") or ""
            )
            if (
                backend_route_id
                and source_route_id
                and backend_route_id != source_route_id
            ):
                raise ValueError(
                    "execution context source belongs to another backend route"
                )
            backend_route_id = source_route_id or backend_route_id
            session_context = source_binding
        return RuntimeSession(
            mode=options.mode,
            binding_id=options.binding_id,
            backend_session_id=backend_session_id,
            backend_route_id=backend_route_id,
            is_new_backend_session=(
                options.mode == "stateless" or not destination_exists
            ),
            context_fingerprint=str(
                session_context.get("context_fingerprint") or ""
            ),
            context_injected_at=str(
                session_context.get("context_injected_at") or ""
            ),
            context_injection_count=int(
                session_context.get("context_injection_count") or 0
            ),
            harness_tool_names=clean_string_tuple(
                session_context.get("harness_tool_names")
            ),
            provider_session_ref=str(session_context.get("provider_session_ref") or ""),
        )

    def complete(
        self,
        request: ExecutionRequest,
        session: RuntimeSession,
        backend_session_id: str,
        *,
        backend_route_id: str = "",
        context_fingerprint: str = "",
        context_attached: bool = False,
        context_injected_at: str = "",
        harness_tool_names: Iterable[object] = (),
    ) -> None:
        """Record the turn's session; an injection counts from when it was sent.

        ``context_injected_at`` is the time the turn's injected parts were
        planned, before the Provider ran it: a compaction during the turn
        compacted them, so it is later than the injection.
        """
        if session.mode == "stateless" or not session.binding_id:
            return
        if (
            request.operation == "release"
            and bool(request.runtime_options.get("delete_binding_on_release"))
        ):
            self.release_session(
                session.binding_id, expected_provider_session_ref=session.provider_session_ref,
            )
            return
        if not backend_session_id:
            return
        route_id = str(backend_route_id or session.backend_route_id or "")
        if (
            session.backend_route_id
            and backend_route_id
            and session.backend_route_id != backend_route_id
            and not session.is_new_backend_session
        ):
            raise ValueError("Backend changed route for an existing session")
        injection_count = session.context_injection_count
        injected_at = session.context_injected_at
        fingerprint = session.context_fingerprint
        if context_attached:
            injection_count += 1
            injected_at = context_injected_at or utc_now()
            fingerprint = context_fingerprint
        self.bind_session(
            session.binding_id,
            provider=request.provider,
            backend_route_id=route_id,
            backend_session_id=backend_session_id,


            context_fingerprint=fingerprint,
            context_injected_at=injected_at,
            context_injection_count=injection_count,
            harness_tool_names=clean_string_tuple((
                *session.harness_tool_names,
                *tuple(harness_tool_names or ()),
            )),
        )

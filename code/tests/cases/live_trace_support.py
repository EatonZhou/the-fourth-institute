"""Live trace fixtures and delivered Harness inputs."""
from __future__ import annotations


from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.backend_contracts import BackendInvocation
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import RuntimeSession

AGENT = "resident_native_record"


def _turn(provider: str = "claude") -> tuple[AgentDataStore, AgentDataProjector, ExecutionRequest, str]:
    store = AgentDataStore()
    route = "" if provider == "kimi" else "one"
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="inspect the code", service_type="agent_session", provider=provider,
        operation="turn", agent_id=AGENT,
        session=SessionOptions(mode="persistent", binding_id=f"resident:{AGENT}", backend_route_id=route),
    )
    session = RuntimeSession(
        mode="persistent", binding_id=request.session.binding_id, backend_session_id="thread-native",
        backend_route_id=route, is_new_backend_session=False,
    )
    projector = AgentDataProjector(store)
    projector.begin(request, session, created_at="2026-10-07T00:00:00.000Z")
    projector.observe_invocation(request.execution_id, BackendInvocation(
        request=request, session=session, user_prompt=request.prompt, system_context="",
        attach_system_context=False, is_new_backend_session=False,
    ), [])
    session_ref = store.latest_provider_session_ref(AGENT)
    return store, projector, request, session_ref

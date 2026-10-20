""":module: spec.design-governance"""
from tests.cases.database_test_support import IntegrityError

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.session_manager import SessionManager


def _bind(
    store: AgentDataStore,
    binding_id: str,
    provider: str,
    backend_session_id: str,
    *,
    backend_route_id: str = "",
) -> dict:
    session = store.ensure_provider_session(
        provider=provider,
        backend_route_id=backend_route_id,
        backend_session_id=backend_session_id,
        agent_id=binding_id,
        actor_ref="test",
    )
    return store.bind_provider_session(
        binding_id,
        session["provider_session_ref"],
    )


def test_provider_binding_contracts(tmp_path, subtests, isolated_portal_case):
    """Provider 会话只能绑定到一个地址，各部署共用同一份对应关系

    同一个 Provider 会话不能同时绑定到两个地址；Dev 与 Release 的 SessionManager 读取同一份 AgentData，看到相同的对应关系和路由。
    """
    with subtests.test(contract='test_provider_session_cannot_be_bound_to_two_agents'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        _bind(store, 'agent-source', 'codex', 'thread-stable')
        with pytest.raises(IntegrityError, match='unique constraint'):
            _bind(store, 'agent-target', 'codex', 'thread-stable')
    with subtests.test(contract='test_session_managers_share_the_agent_data_authority'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = AgentDataStore()
        dev = SessionManager(agent_data=store)
        release = SessionManager(agent_data=store)
        binding_id = 'agent-shared'
        _bind(store, binding_id, 'codex', 'thread-shared', backend_route_id='one')
        continued = release.prepare(ExecutionRequest.new(actor='sample_admin', prompt='continue from release', service_type='agent_session', provider='codex', operation='turn', session=SessionOptions(mode='persistent', binding_id=binding_id)))
        assert dev.provider_binding(binding_id) == release.provider_binding(binding_id)
        assert continued.backend_session_id == 'thread-shared'
        assert continued.backend_route_id == 'one'

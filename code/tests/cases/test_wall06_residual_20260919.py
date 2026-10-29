from management_portal.society_service import resident_conversation_stream
from types import SimpleNamespace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import (
    conversation_projection, lineage_domain, resident_protocol, server,
)
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.society_service.runtime.contracts import ResidentTerminalFact
from management_portal.society_service.state_store import LineageDataStore
from management_portal.society_service import lineage_aggregate
from tests.cases.database_test_support import register_owned_workspace


RESIDENT = 'resident_060606060606'
LINEAGE = 'lineage_060606060606'



@pytest.fixture
def repository(tmp_path, monkeypatch):
    store = LineageDataStore()
    store.database.write(lambda connection: connection.execute(
        "INSERT INTO residents(resident_id, title, backend, created_at, updated_at, created_by, owned_workspace_id) VALUES (%s, '', 'codex', NULL, NULL, '', %s)",
        (RESIDENT, register_owned_workspace(connection, RESIDENT)),
    ))
    repository = ResidentConversationRepository(store, AgentDataStore())
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: repository)
    return repository


def append(repository, message_id, role='user', **extra):
    return repository.append_message(RESIDENT, {
        'message_id': message_id, 'role': role,
        'author_id': 'fixture-user' if role == 'user' else RESIDENT,
        'content_parts': [{'type': 'text', 'text': 'synthetic fixture'}], **extra,
    })


def test_artifact_read_uses_authorized_resident_and_real_repository(repository, monkeypatch):
    """产物读取先校验 Resident 权限且拒绝跨节点产物"""
    append(repository, 'message_fixture_artifact', 'agent', provider_response={'safe': 42})
    public = repository.public_message(RESIDENT, 'message_fixture_artifact')
    artifact_id = next(part['artifact_id'] for part in public['content_parts'] if part['type'] == 'artifact_reference')

    def target(resident_id, actor):
        if resident_id != RESIDENT or actor != 'fixture-reader':
            raise PermissionError('fixture Resident read denied')
        return SimpleNamespace(resident_id=resident_id)

    monkeypatch.setattr(resident_protocol, '_target', target)
    result = resident_protocol.get_resident_artifact(RESIDENT, 'fixture-reader', artifact_id)
    assert result['value'] == {'safe': 42}
    with pytest.raises(PermissionError):
        resident_protocol.get_resident_artifact(RESIDENT, 'fixture-denied', artifact_id)
    with pytest.raises(KeyError):
        resident_protocol.get_resident_artifact(RESIDENT, 'fixture-reader', 'artifact_missing')
    with pytest.raises(KeyError):
        repository.artifact('resident_070707070707', artifact_id)


@pytest.mark.parametrize('fault', ['', 'coordinator', 'housekeeping', 'relay', 'projection', 'lifecycle', 'stopped'])
def test_health_keeps_subsystem_diagnostics_when_degraded(monkeypatch, fault):
    """上下文差异读取校验两侧快照且遵循消息与字符限制"""
    instance = server.SocietyServiceServer.__new__(server.SocietyServiceServer)
    coordinator = {'thread_alive': True, 'last_error': 'bad' if fault == 'coordinator' else '',
                   'last_housekeeping_error': 'bad' if fault == 'housekeeping' else ''}
    relay = {'running': fault != 'stopped', 'last_error': 'bad' if fault == 'relay' else ''}
    projection = {'running': True, 'last_error': 'bad' if fault == 'projection' else ''}
    lifecycle = {'last_error': 'bad' if fault == 'lifecycle' else ''}
    instance.coordinator = SimpleNamespace(snapshot=lambda: coordinator)
    instance.resident_conversation_event_relay = SimpleNamespace(health=lambda: relay)
    instance.resident_resource_projection = SimpleNamespace(health=lambda: projection)
    instance.read_pool = SimpleNamespace(health=lambda: {'workers': 0})
    instance.conversation_index_pool = SimpleNamespace(health=lambda: {'workers': 0})
    instance.publisher = SimpleNamespace(health=lambda: {'enabled': False})
    instance.projections_enabled = True
    instance.started_at = 'fixture'
    monkeypatch.setattr(server, '_domain_module', lambda name: SimpleNamespace(
        resident_lifecycle_health=lambda: lifecycle, state_lock_health=lambda: {'held': 0},
    ))
    result = instance.dispatch('health', {})
    assert result['ok'] is (fault == '')
    assert result['coordinator'] == coordinator
    assert result['resident_conversation_event_relay'] == relay
    assert result['resident_resource_projection'] == projection
    assert result['resident_lifecycle'] == lifecycle
    assert result['read_pool'] == {'workers': 0}
    assert {'pid', 'build_id', 'lineage_data', 'state_locks', 'notifications'} <= result.keys()


def test_terminal_fact_normalizes_status_and_rejects_invalid_fact_before_consumption():
    """Resident 终态事实统一状态并在归并前拒绝无效内容

    canceled 与 interrupted 按 cancelled 处理；未知状态、超长 business_ref、缺少 actor 或 message_id 的终态事实在交给 Lineage 运行器之前被拒绝。

    :module: lineage.service
    :design: decision_1b2bdf5c33d6
    """
    payload = {'resident_id': RESIDENT, 'actor': RESIDENT, 'message_id': 'message_fixture', 'lineage_id': LINEAGE}
    observed = [ResidentTerminalFact.from_payload({**payload, 'terminal_status': status, 'business_ref': 'x' * 500})
                for status in ['completed', 'failed', 'cancelled', 'canceled', 'interrupted']]
    assert [fact.terminal_status for fact in observed] == ['completed', 'failed', 'cancelled', 'cancelled', 'cancelled']
    assert all(fact.actor == RESIDENT and fact.business_ref == 'x' * 500 for fact in observed)
    for changes in [{'terminal_status': 'running'}, {'business_ref': 'x' * 501}, {'actor': ''}, {'message_id': ''}]:
        with pytest.raises(ValueError):
            ResidentTerminalFact.from_payload({**payload, **changes})


def test_membership_rejects_deleted_foreign_and_nonleader(monkeypatch):
    """Lineage 成员授权拒绝已删除、外属与非 Leader 身份"""
    member = {'lineage_id': LINEAGE}
    monkeypatch.setattr(lineage_aggregate, 'read_lineage_state', lambda lineage_id: {'leader_resident_id': RESIDENT})
    monkeypatch.setattr(lineage_domain.resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda principal: member))
    assert lineage_domain.authorize_lineage_resident_principal(LINEAGE, RESIDENT, require_leader=True) == RESIDENT
    with pytest.raises(PermissionError):
        lineage_domain.authorize_lineage_resident_principal(LINEAGE, 'owner-name')
    with pytest.raises(PermissionError, match='Leader'):
        lineage_domain.authorize_lineage_resident_principal(LINEAGE, 'resident_070707070707', require_leader=True)
    member['deleted_at'] = 'fixture'
    with pytest.raises(PermissionError):
        lineage_domain.authorize_lineage_resident_principal(LINEAGE, RESIDENT)
    member.pop('deleted_at')
    member['lineage_id'] = 'lineage_070707070707'
    with pytest.raises(PermissionError):
        lineage_domain.authorize_lineage_resident_principal(LINEAGE, RESIDENT)


def test_context_delta_and_bounds_use_both_snapshots(monkeypatch):
    """服务健康降级时保留各子系统诊断信息"""
    relative = 'resident_070707070707'
    calls = []
    def snapshot(resource_id, actor, **_kwargs):
        calls.append((resource_id, actor))
        if actor != 'fixture-reader':
            raise PermissionError('fixture read denied')
        texts = ['common'] if resource_id == relative else ['common', 'middle', 'last-message']
        return {'id': resource_id, 'resource_type': 'resident', 'title': 'fixture',
                'state': 'idle', 'lineage_id': LINEAGE,
                'messages': [{'role': 'user', 'content': text, '_digest': text} for text in texts]}
    monkeypatch.setattr(conversation_projection, '_snapshot', snapshot)
    result = conversation_projection.read_context(RESIDENT, actor='fixture-reader', relative_to_address=relative, max_messages=1, max_chars=4)
    assert result['context_mode'] == 'lineage_delta'
    assert result['common_message_count'] == 1
    assert result['returned_message_count'] == 1
    assert result['returned_char_count'] == 4
    assert result['messages'][0]['content'] == 'last'
    assert '_digest' not in result['messages'][0]
    assert calls == [(RESIDENT, 'fixture-reader'), (relative, 'fixture-reader')]
    with pytest.raises(PermissionError):
        conversation_projection.read_context(RESIDENT, actor='fixture-denied')
    with pytest.raises(ValueError):
        conversation_projection.read_context(RESIDENT, actor='fixture-reader', max_messages=0)

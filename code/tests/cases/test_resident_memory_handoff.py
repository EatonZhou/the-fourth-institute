"""Two lazy memory transfers preserve the canonical Resident trace."""
import copy
from unittest.mock import Mock

import pytest
from management_portal.society_service import (
    resident_memory_handoff as memory, resident_pending_session_context as pending,
    resident_restart_domain as restart, resident_backend, resident_record_domain,
    resident_runtime_domain, resident_catalog, resident_session_backend_domain as sessions,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.fork_context_test_support import record_session_turn


@pytest.mark.parametrize('provider,route,method', [('codex','zero','native'), ('codex','one','local'), ('claude','default','local')])
def test_available_transfer_default_and_explicit_rejection(provider, route, method):
    """默认优先同账号原生 Fork，不支持的显式方法拒绝"""
    source = {'provider':'codex','account_id':'zero','has_session':True}
    assert memory.choose(source, provider, route) == method
    if method == 'local':
        with pytest.raises(ValueError): memory.choose(source, provider, route, 'native')
    for old in ('preserve','smart','compact','visible','fresh','full','recent-5','message'):
        with pytest.raises(ValueError): memory.choose(source, provider, route, old)


def test_local_context_has_bounded_existing_summary_and_last_three_rounds():
    """本地交接只含已有摘要与末三轮，原生交接只含索引"""
    detail = {'messages': [
        {'role':role,'content_parts':[{'type':'text','text':f'{role}-round-{i} '+ 'x'*1300}]}
        for i in range(5) for role in ('user','agent')]}
    local = memory.material({'resident_id':'resident_aaaaaaaaaaaa'},detail,'local','summary '*1000)
    assert 'round-0' not in local and 'round-1' not in local
    assert all(f'round-{i}' in local for i in (2,3,4))
    assert len(local) < 6500 and 'GetMessageArtifact' in local
    native = memory.material({'resident_id':'resident_aaaaaaaaaaaa'},detail,'native','secret summary')
    assert 'round-' not in native and 'secret summary' not in native


@pytest.mark.parametrize('method',['native','local'])
def test_fork_only_persists_intent_and_trace_then_defers_native_until_input(resident_record, monkeypatch, method):
    """Fork 不提交模型工作；复制 trace，首次输入才执行一次原生 Fork"""
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.society_service.resident_fork_domain import create_resident_full_context_fork
    store = get_agent_data_store()
    record_session_turn(resident_record)
    before = copy.deepcopy(store.agent_context_snapshot(resident_record['resident_id']))
    source_binding = copy.deepcopy(resident_backend.session_record(resident_record))
    submit = Mock(side_effect=AssertionError('creation must not execute'))
    monkeypatch.setattr(sessions,'run_backend_session_operation',submit)
    created = create_resident_full_context_fork(resident_record['resident_id'],'CASE-A',memory_method=method)
    child = resident_catalog.get_catalog().get(created['resident_id'])
    assert child['fork_mode'] == method
    assert resident_backend.session_record(child) == {}
    assert child['session_restart']['model_operation'] is False
    assert store.agent_context_snapshot(resident_record['resident_id']) == before
    assert resident_backend.session_record(resident_record) == source_binding
    assert [s['segment_id'] for s in store.agent_context_snapshot(child['resident_id'])['segments']] == [s['segment_id'] for s in before['segments']]
    assert not submit.called
    def native_fork(**kwargs):
        assert kwargs['operation'] == 'fork' and 'prompt' not in kwargs
        assert kwargs['metadata']['expected_source_binding']['provider_session_ref'] == source_binding['provider_session_ref']
        execution_port.sessions().bind_session(kwargs['binding_id'],provider='codex',backend_route_id=child['backend_route_id'],backend_session_id='child-native-thread')
    submit.side_effect = native_fork
    memory.prepare_native(child,'CASE-A')
    memory.prepare_native(child,'CASE-A')
    assert submit.call_count == (1 if method == 'native' else 0)
    seed = pending.context_seed(child,'real new message',accepted=False)
    assert seed['portable_prompt'].endswith('real new message')
    assert pending.context_seed(child,'next',accepted=True) == {}
    reset = {**child,'context_reset_at':'2099-01-01T00:00:00Z'}
    assert pending.context_seed(reset,'cleared',accepted=False) == {}


def test_native_source_replacement_is_not_silently_followed(resident_record, monkeypatch):
    """首条输入前来源绑定改变时拒绝使用另一会话"""
    from management_portal.society_service.resident_fork_domain import create_resident_full_context_fork
    record_session_turn(resident_record)
    created = create_resident_full_context_fork(resident_record['resident_id'],'CASE-A',memory_method='native')
    child = resident_catalog.get_catalog().get(created['resident_id'])
    resident_backend.delete_session_identity(resident_record)
    execute = Mock(); monkeypatch.setattr(sessions,'run_backend_session_operation',execute)
    with pytest.raises(ValueError,match='绑定已改变'): memory.prepare_native(child,'CASE-A')
    assert not execute.called


@pytest.mark.parametrize('requested',['','local'])
def test_switch_is_configuration_only_and_does_not_reset_trace(monkeypatch,requested):
    """切换只存配置及交接意图，不提交执行或重建 Segment"""
    from tests.cases.test_session_restart import _restart_domain
    from management_portal.execution.agent_data_store import AgentDataStore
    record, operations, saved = _restart_domain(monkeypatch)
    monkeypatch.setattr(memory,'existing_summary',lambda *_:'saved personal summary')
    before = copy.deepcopy(AgentDataStore().agent_context_snapshot(record['resident_id']))
    result = restart.restart_resident_session(record['resident_id'],'alice',{
        'operation':'switch_account','target_provider':'codex','target_account':'one',
        'context_strategy':requested,'idempotency_key':'memory-switch'})
    assert result['status'] == 'completed' and result['model_operation'] is False
    assert result['pending_handoff']['strategy'] == 'local'
    assert 'source_binding_id' not in result['pending_handoff']
    assert not operations
    assert AgentDataStore().agent_context_snapshot(record['resident_id']) == before
    assert restart.restart_resident_session(record['resident_id'],'alice',{
        'operation':'switch_account','target_provider':'codex','target_account':'one',
        'context_strategy':requested,'idempotency_key':'memory-switch'}) == result


@pytest.mark.parametrize('operation', ['switch_account', 'restart'])
def test_native_handoff_is_rejected_outside_fork_without_side_effects(monkeypatch, operation):
    """即使原生 Fork 可用，普通切换也不能创建分支或替换原绑定"""
    from tests.cases.test_session_restart import _restart_domain
    record, operations, saved = _restart_domain(monkeypatch)
    binding = copy.deepcopy(resident_backend.session_record(record))
    with pytest.raises(ValueError, match='厂商原生仅用于 Fork'):
        restart.restart_resident_session(record['resident_id'], 'alice', {
            'operation': operation, 'target_provider': 'codex', 'target_account': 'zero',
            'context_strategy': 'native'})
    assert resident_backend.session_record(record) == binding
    assert not operations and not saved


def test_same_account_settings_cannot_accidentally_detach_session(monkeypatch):
    """参数修改必须走设置 owner；切换接口不替换会话或创建交接意图"""
    from tests.cases.test_session_restart import _restart_domain
    record, operations, saved = _restart_domain(monkeypatch)
    binding = copy.deepcopy(resident_backend.session_record(record))
    body = {'operation': 'switch_account', 'target_provider': 'codex', 'target_account': 'zero'}
    assert restart.restart_resident_session(record['resident_id'], 'alice', body)['no_op']
    with pytest.raises(ValueError, match='UpdateResidentSettings'):
        restart.restart_resident_session(record['resident_id'], 'alice', {**body, 'model': 'gpt-6-astra', 'reasoning_effort': 'high'})
    assert resident_backend.session_record(record) == binding
    assert 'session_restart' not in record
    assert not operations and not saved


def test_account_catalog_separates_migration_from_fork(monkeypatch):
    """同账号也只有 Fork 提供原生选项；迁移目录永远只提供摘要重组"""
    from tests.cases.test_session_restart import _model_api_restart
    record, operations, saved = _model_api_restart(monkeypatch)
    catalog = restart.get_resident_restart_options(record['resident_id'], 'alice')
    for accounts in catalog['accounts'].values():
        for account in accounts:
            assert [row['id'] for row in account['context_options']] == ['local']
            assert account['context_options'][0]['label'] == '摘要重组'
            assert [row['id'] for row in account['fork_context_options']] == ['native', 'local']
    assert catalog['accounts']['codex'][0]['fork_context_options'][0]['enabled']
    assert not catalog['accounts']['provider_api'][0]['fork_context_options'][0]['enabled']
    assert not operations and not saved


def test_first_real_input_carries_core_and_indices_once_after_acceptance(resident_record, monkeypatch):
    """首次真实输入携带规则和短交接；受理后不再附加历史材料"""
    from management_portal.society_service.resident_fork_domain import create_resident_full_context_fork
    from management_portal.society_service import resident_execution_domain as execution
    from management_portal.execution.agent_data_store import get_agent_data_store
    record_session_turn(resident_record, question='canonical question', answer='canonical answer')
    child_id = create_resident_full_context_fork(resident_record['resident_id'],'CASE-A',memory_method='local')['resident_id']
    child = resident_catalog.get_catalog().get(child_id)
    monkeypatch.setattr(execution, '_prepare_resident_prompt', lambda r,j:{**j,'actual_sent_prompt':j['raw_message']})
    job = {'actor':'CASE-A','raw_message':'new user task'}
    prepared = execution._portable_context_execution(child,job)
    prompt = prepared['fork_context_input']['portable_prompt']
    assert 'canonical question' in prompt and 'canonical answer' in prompt
    assert prompt.endswith('new user task') and prepared['memory_handoff']
    parts = {p.name:p for p in execution.resident_system_instructions(child,prepared).parts}
    assert parts['core_rules'].timing == 'every_turn'
    assert parts['runtime_identity'].timing == 'every_turn'
    assert child_id in parts['runtime_identity'].content
    # Session creation is not acceptance; a failed attempt retains the same seed.
    manager = execution_port.sessions()
    manager.bind_session(resident_backend.execution_binding_id(child),provider='codex',backend_route_id='zero',backend_session_id='new-local')
    assert execution._portable_context_execution(child,job)['fork_context_input'] == prepared['fork_context_input']
    store = get_agent_data_store(); eid='execution-accepted-memory'
    store.begin_execution(execution_id=eid,agent_id=child_id,actor_ref='CASE-A',provider='codex',started_at='2026-11-24T00:00:00Z')
    ref = resident_backend.session_record(child)['provider_session_ref']
    store.bind_execution_provider_session(eid,provider_session_ref=ref)
    store.record_input_acceptance(eid,agent_id=child_id,provider_turn_id='accepted-turn')
    subsequent = execution._portable_context_execution(child,job)
    assert 'fork_context_input' not in subsequent and 'memory_handoff' not in subsequent


def test_native_fork_starts_own_instruction_counter(resident_record):
    """厂商继承历史不继承父会话规则注入计数"""
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.session_manager import SessionManager
    manager = SessionManager()
    record_session_turn(resident_record)
    source_id = resident_backend.execution_binding_id(resident_record)
    source = manager.provider_binding(source_id)
    request = ExecutionRequest.new(actor='CASE-A',prompt='Fork session metadata only',service_type='agent_session',provider='codex',operation='fork',
        session=SessionOptions(mode='persistent',binding_id='resident:resident_bbbbbbbbbbbb',context_source_binding_id=source_id,backend_route_id='zero'))
    session = manager.prepare(request)
    assert session.context_injection_count == 0 and not session.context_fingerprint
    assert session.backend_session_id == source['backend_session_id']
    assert manager.provider_binding(source_id) == source


def test_old_committed_handoff_remains_readable_but_new_old_mode_is_rejected(resident_record):
    """兼容已提交旧交接读回，不开放旧创建方法"""
    state = {'restart_id':'old','configuration_only':True,'status':'completed','context_strategy':'compact',
        'pending_handoff':{'operation_id':'old','strategy':'compact','context_reset_at':'',
            'target':{'provider':'codex','route':'zero'},'prompt':'old frozen material'}}
    record = {**resident_record,'backend_route_id':'zero','session_restart':state,'context_reset_at':''}
    assert 'old frozen material' in pending.context_seed(record,'new task',accepted=False)['portable_prompt']
    state['status']='accepted'
    with pytest.raises(ValueError,match='unsupported'): pending.context_seed(record,'new task',accepted=False)


def test_shared_memory_controls_in_browser_dom():
    """界面按稳定账号读模型目录，并保持两方法和过期响应隔离"""
    import subprocess
    from pathlib import Path
    result = subprocess.run(['node',str(Path(__file__).with_name('javascript')/'memory_handoff_controls.cjs')],capture_output=True,text=True,timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_configuration_save_failure_restores_source_binding(monkeypatch):
    """摘要重组配置保存失败保持原绑定与原 trace"""
    from tests.cases.test_resident_account_switch_protocol import _fresh_bound_domain
    record, operations, saved, store, resets = _fresh_bound_domain(monkeypatch)
    before = copy.deepcopy(resident_backend.session_record(record))
    trace = copy.deepcopy(store.agent_context_snapshot(record['resident_id']))
    def fail(*args, **kwargs): raise RuntimeError('save failed')
    monkeypatch.setattr(resident_record_domain, 'save_resident_record', fail)
    with pytest.raises(RuntimeError, match='save failed'):
        restart.restart_resident_session(record['resident_id'],'alice',{'context_strategy':'local'})
    restored = resident_backend.session_record(record)
    assert restored['provider_session_ref'] == before['provider_session_ref']
    assert store.agent_context_snapshot(record['resident_id']) == trace
    assert not operations and not resets


@pytest.mark.parametrize('operation',['restart','switch_account'])
def test_switch_never_replays_failed_input(monkeypatch, operation):
    record, operations, saved = __import__('tests.cases.test_session_restart',fromlist=['_restart_domain'])._restart_domain(monkeypatch)
    with pytest.raises(ValueError,match='不重派失败输入'):
        restart.restart_resident_session(record['resident_id'],'alice',{
            'operation':operation,'context_strategy':'local','resume_failed_input':True})
    assert not operations and not saved


@pytest.mark.parametrize('requested', ['', 'native'])
def test_admitted_native_restart_survives_contract_change(monkeypatch, requested):
    """旧受理意图按精确原提交者/请求续接，不开放新 native 迁移"""
    from tests.cases.test_session_restart import _restart_domain
    record, operations, saved = _restart_domain(monkeypatch)
    body = {'operation': 'restart', 'target_provider': 'codex', 'target_account': 'zero',
            'context_strategy': requested, 'idempotency_key': 'old-native:execute'}
    record['session_restart'] = {
        'restart_id': 'restart_old', 'request_id': 'old-native', 'status': 'accepted',
        'intent': {'context_strategy': 'native'},
        'deferred': {'request_id': 'old-native', 'actor': 'alice', 'payload': copy.deepcopy(body), 'phase': 'waiting'},
    }
    binding = copy.deepcopy(resident_backend.session_record(record))
    with pytest.raises(ValueError, match='原提交者与原请求'):
        restart.restart_resident_session(record['resident_id'], 'bob', body)
    with pytest.raises(ValueError, match='原提交者与原请求'):
        restart.restart_resident_session(record['resident_id'], 'alice', {**body, 'model': 'gpt-6-astra'})
    assert resident_backend.session_record(record) == binding and not operations and not saved
    result = restart.restart_resident_session(record['resident_id'], 'alice', body)
    assert result['restart_id'] == 'restart_old'
    assert result['pending_handoff']['strategy'] == 'native'
    assert result['model_operation'] is False and not operations

"""Account changes reuse full Fork context, without shortening canonical history."""
import copy
import pytest
from management_portal.society_service import (
    resident_backend, resident_execution_domain as execution,
    resident_pending_session_context as pending, resident_restart_domain as restart,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import _restart_domain
from tests.cases.test_resident_account_switch_protocol import _fresh_bound_domain


@pytest.mark.parametrize('strategy', ['', 'preserve'])
def test_switch_reuses_complete_fork_seed_until_accepted(monkeypatch, strategy):
    record, operations, saved, store, resets = _fresh_bound_domain(monkeypatch)
    rid = record['resident_id']
    for i in range(6):
        store.append_agent_segments(rid, [
            store.put_segment(segment_type='input', content=f'user-round-{i}'),
            store.put_segment(segment_type='model', content=f'answer-round-{i}'),
        ], actor_ref='alice')
    tool = store.put_segment(segment_type='tool', content_type='application/json', content={
        'tool_name': 'inspect', 'call_id': 'original-call', 'input': {'path': '/data'}, 'result': 'original-tool-result',
    })
    store.append_agent_segments(rid, [tool], actor_ref='alice')
    before = copy.deepcopy(store.agent_context_snapshot(rid))
    body = {'operation': 'switch_account', 'target_provider': 'codex', 'target_account': 'one',
            'context_strategy': strategy, 'idempotency_key': 'switch-full-context'}
    result = restart.restart_resident_session(rid, 'alice', body)
    current = saved[-1]
    assert result['context_strategy'] == 'preserve' and result['model_operation'] is False
    assert 'pending_handoff' not in result
    assert store.agent_context_snapshot(rid) == before
    assert not operations and not resets
    assert restart.restart_resident_session(rid, 'alice', body) == result
    monkeypatch.setattr(execution, '_prepare_resident_prompt', lambda r, j: {**j, 'actual_sent_prompt': j['raw_message']})
    job = {'actor': 'alice', 'raw_message': 'continue the original task'}
    seed = execution._portable_context_execution(current, job)['fork_context_input']
    assert [s['segment_id'] for s in seed['segments']] == [s['segment_id'] for s in before['segments']]
    assert seed['prompt'] == job['raw_message']
    assert tool in [s['segment_id'] for s in seed['segments']]
    manager = execution_port.sessions()
    manager.bind_session(resident_backend.execution_binding_id(current), provider='codex',
                         backend_route_id='one', backend_session_id='new-account-thread')
    assert execution._portable_context_execution(current, job)['fork_context_input'] == seed
    eid = 'execution-accepted-switch'
    store.begin_execution(execution_id=eid, agent_id=rid, actor_ref='alice', provider='codex', started_at='2026-11-25T00:00:00Z')
    store.bind_execution_provider_session(eid, provider_session_ref=resident_backend.session_record(current)['provider_session_ref'])
    store.record_input_acceptance(eid, agent_id=rid, provider_turn_id='accepted-turn')
    assert execution._portable_context_execution(current, job)['fork_context_input'] == seed
    store.record_context_received(rid, resident_backend.session_record(current)['provider_session_ref'],
                                  [s['segment_id'] for s in seed['segments']], actor_ref='alice')
    assert 'fork_context_input' not in execution._portable_context_execution(current, job)


def test_initialized_compacted_fork_reseeds_after_switch(monkeypatch):
    from tests.cases.test_resident_account_switch_protocol import _effective_history
    record, operations, saved, store, resets = _fresh_bound_domain(monkeypatch)
    record.update(context_compaction_policy='initial', context_compaction_state='completed', context_compacted_at='2026-11-24T00:00:00Z')
    _effective_history(record['resident_id'])
    restart.restart_resident_session(record['resident_id'], 'alice', {'target_account': 'one'})
    current = saved[-1]
    assert not any(k in current for k in ('context_compaction_policy', 'context_compaction_state', 'context_compacted_at'))
    monkeypatch.setattr(execution, '_prepare_resident_prompt', lambda r, j: {**j, 'actual_sent_prompt': j['raw_message']})
    from tests.cases.test_resident_fork_context_delivery import _delivered_window
    prompt = _delivered_window(execution._portable_context_execution(current, {'raw_message': 'continue'}))
    assert 'kept summary' in prompt and 'original-call' in prompt
    assert 'pre-compaction secret' not in prompt
    assert not operations and not resets


def test_missing_context_rejected_before_detaching(monkeypatch):
    record, operations, saved = _restart_domain(monkeypatch)
    binding = copy.deepcopy(resident_backend.session_record(record))
    monkeypatch.setattr(execution_port, 'agent_context_snapshot', lambda *_: {})
    with pytest.raises(ValueError, match='缺少 PostgreSQL'):
        restart.restart_resident_session(record['resident_id'], 'alice', {'target_account': 'one'})
    assert resident_backend.session_record(record) == binding
    assert not operations and not saved


@pytest.mark.parametrize('strategy', ['native', 'local'])
def test_existing_short_handoff_uses_full_canonical_history(monkeypatch, strategy):
    record, operations, saved, store, resets = _fresh_bound_domain(monkeypatch)
    restart.restart_resident_session(record['resident_id'], 'alice', {'target_account': 'one'})
    current = saved[-1]
    current['session_restart']['pending_handoff'] = {'strategy': strategy, 'prompt': 'truncated summary'}
    monkeypatch.setattr(execution, '_prepare_resident_prompt', lambda r, j: {**j, 'actual_sent_prompt': j['raw_message']})
    seed = execution._portable_context_execution(current, {'raw_message': 'continue'})['fork_context_input']
    assert [s['segment_id'] for s in seed['segments']] == [
        s['segment_id'] for s in store.agent_context_snapshot(current['resident_id'])['segments']]
    assert seed['prompt'] == 'continue'


@pytest.mark.parametrize('strategy', ['native', 'local'])
def test_new_short_handoff_rejected_without_side_effects(monkeypatch, strategy):
    record, operations, saved = _restart_domain(monkeypatch)
    binding = copy.deepcopy(resident_backend.session_record(record))
    with pytest.raises(ValueError, match='使用 preserve'):
        restart.restart_resident_session(record['resident_id'], 'alice', {'context_strategy': strategy})
    assert resident_backend.session_record(record) == binding
    assert not operations and not saved


def test_same_account_settings_do_not_detach_session(monkeypatch):
    record, operations, saved = _restart_domain(monkeypatch)
    binding = copy.deepcopy(resident_backend.session_record(record))
    body = {'operation': 'switch_account', 'target_provider': 'codex', 'target_account': 'zero'}
    assert restart.restart_resident_session(record['resident_id'], 'alice', body)['no_op']
    with pytest.raises(ValueError, match='UpdateResidentSettings'):
        restart.restart_resident_session(record['resident_id'], 'alice', {**body, 'model': 'gpt-6-astra'})
    assert resident_backend.session_record(record) == binding
    assert not operations and not saved


@pytest.mark.parametrize('requested', ['', 'native', 'local'])
def test_admitted_restart_survives_contract_change(monkeypatch, requested):
    record, operations, saved = _restart_domain(monkeypatch)
    body = {'operation': 'restart', 'target_provider': 'codex', 'target_account': 'zero',
            'context_strategy': requested, 'idempotency_key': 'old-switch:execute'}
    record['session_restart'] = {
        'restart_id': 'restart_old', 'request_id': 'old-switch', 'status': 'accepted',
        'intent': {'context_strategy': requested or 'native'},
        'deferred': {'request_id': 'old-switch', 'actor': 'alice', 'payload': copy.deepcopy(body), 'phase': 'waiting'},
    }
    with pytest.raises(ValueError, match='原提交者与原请求'):
        restart.restart_resident_session(record['resident_id'], 'bob', body)
    with pytest.raises(ValueError, match='原提交者与原请求'):
        restart.restart_resident_session(record['resident_id'], 'alice', {**body, 'model': 'gpt-6-astra'})
    result = restart.restart_resident_session(record['resident_id'], 'alice', body)
    assert result['restart_id'] == 'restart_old'
    assert result['context_strategy'] == 'preserve' and 'pending_handoff' not in result
    assert result['model_operation'] is False and not operations


def test_old_committed_compaction_material_remains_readable(monkeypatch):
    record, _, _ = _restart_domain(monkeypatch)
    record.update(backend_route_id='zero', context_reset_at='', session_restart={
        'restart_id': 'old', 'configuration_only': True, 'status': 'completed', 'context_strategy': 'compact',
        'pending_handoff': {'operation_id': 'old', 'strategy': 'compact', 'context_reset_at': '',
                            'target': {'provider': 'codex', 'route': 'zero'}, 'prompt': 'old frozen material'},
    })
    assert pending.validate_handoff(record) is None

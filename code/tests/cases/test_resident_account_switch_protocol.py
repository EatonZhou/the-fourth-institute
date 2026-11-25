"""Account switch admission and lifecycle, with isolated PostgreSQL history."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_restart_domain as domain
from management_portal.society_service import resident_backend, resident_execution_domain
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import _restart_domain
from management_portal.execution.agent_data_store import AgentDataStore


def _effective_history(agent_id):
    store = AgentDataStore()
    obsolete = store.put_segment(segment_type='input', content='pre-compaction secret')
    compact = store.put_segment(segment_type='compaction', content_type='application/json', content={
        'replacement_history': [{'type': 'message', 'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'kept summary'},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,ZmFrZQ=='},
        ]}],
    })
    tool = store.put_segment(segment_type='tool', content_type='application/json', content={
        'tool_name': 'inspect', 'call_id': 'original-call', 'input': {'path': '/a'}, 'result': 'done',
    })
    store.append_agent_segments(agent_id, [obsolete, compact, tool], actor_ref='test')
    return store, [compact, tool]


def test_same_account_switch_is_noop(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        original = copy.deepcopy(record)
        result = domain.restart_resident_session(record['resident_id'], 'alice', {
            'operation': 'switch_account', 'target_account': 'zero',
        })
        assert result['status'] == 'completed' and result['no_op'] is True
        assert result['actual_binding']['backend_session_id'] == 'session_old'
        assert operations == [] and saved == [] and record == original


def test_default_restart_preserves_without_model_turn(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        result = domain.restart_resident_session(record['resident_id'], 'alice', {})
        assert result['context_strategy'] == 'preserve'
        assert result['operation'] == 'restart'
        assert result['operation_id'] == result['restart_id']
        assert operations == []
        assert result['model_operation'] is False
        assert result['resume_failed_input'] is False


def _fresh_bound_domain(monkeypatch):
    from management_portal.execution.session_manager import SessionManager

    real_session_record = resident_backend.session_record
    record, operations, saved = _restart_domain(monkeypatch)
    record['session_state'] = 'active'
    store = AgentDataStore()
    sessions = SessionManager(agent_data=store)
    binding_id = resident_backend.execution_binding_id(record)
    source = store.provider_binding(binding_id)
    store.delete_provider_binding(binding_id, expected_provider_session_ref=source['provider_session_ref'])
    old = store.ensure_provider_session(provider='codex', backend_route_id='zero',
                                        backend_session_id='old-native-thread',
                                        agent_id=record['resident_id'], actor_ref='test')
    store.bind_provider_session(resident_backend.execution_binding_id(record), old['provider_session_ref'])
    monkeypatch.setattr(execution_port, 'sessions', lambda: sessions)
    monkeypatch.setattr(resident_backend, 'session_record', real_session_record)
    resets = []
    def reset(agent_id, **kwargs):
        resets.append((agent_id, kwargs))
        return store.reset_provider_context(agent_id, **kwargs)
    monkeypatch.setattr(execution_port, 'reset_provider_context', reset)
    return record, operations, saved, store, resets




@pytest.mark.parametrize('changed', ['configuration', 'binding'])
def test_fresh_rechecks_source_before_clearing(monkeypatch, isolated_portal_case, changed):
    with isolated_portal_case():
        record, operations, saved, store, resets = _fresh_bound_domain(monkeypatch)
        read_binding = resident_backend.session_record
        original = copy.deepcopy(read_binding(record))
        def change_at_check(current, *, expected_binding=None):
            if expected_binding is not None:
                if changed == 'configuration':
                    record['execution_settings'] = {'model': 'changed-concurrently'}
                    current['execution_settings'] = copy.deepcopy(record['execution_settings'])
                else:
                    replacement = store.ensure_provider_session(provider='codex', backend_route_id='zero',
                        backend_session_id='concurrent-thread', agent_id=record['resident_id'], actor_ref='test')
                    store.delete_provider_binding(original['binding_id'],
                        expected_provider_session_ref=original['provider_session_ref'])
                    store.bind_provider_session(original['binding_id'], replacement['provider_session_ref'])
            return read_binding(current, expected_binding=expected_binding)
        monkeypatch.setattr(resident_backend, 'session_record', change_at_check)
        with pytest.raises((ValueError, RuntimeError), match='(configuration|binding).*changed'):
            domain.restart_resident_session(record['resident_id'], 'alice', {'context_strategy': 'preserve'})
        actual = read_binding(record)
        assert actual['backend_session_id'] == ('old-native-thread' if changed == 'configuration' else 'concurrent-thread')
        assert record['session_state'] == 'active' and saved == [] and operations == [] and resets == []
        assert store.agent_context_snapshot(record['resident_id'])['segments']


def test_fresh_save_failure_restores_detached_binding(monkeypatch, isolated_portal_case):
    from management_portal.society_service import resident_record_domain
    with isolated_portal_case():
        record, operations, saved, store, resets = _fresh_bound_domain(monkeypatch)
        original = copy.deepcopy(resident_backend.session_record(record))
        def fail_save(*_args, **_kwargs):
            raise RuntimeError('save failed')
        monkeypatch.setattr(resident_record_domain, 'save_resident_record', fail_save)
        with pytest.raises(RuntimeError, match='save failed'):
            domain.restart_resident_session(record['resident_id'], 'alice', {'context_strategy': 'preserve'})
        restored = resident_backend.session_record(record)
        assert {k: v for k, v in restored.items() if k not in {'created_at', 'updated_at'}} == {
            k: v for k, v in original.items() if k not in {'created_at', 'updated_at'}
        }
        assert record['session_state'] == 'active' and operations == [] and resets == []
        assert store.agent_context_snapshot(record['resident_id'])['segments']


def test_switch_rejects_context_reduction(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        with pytest.raises(ValueError, match='使用 preserve'):
            domain.restart_resident_session(record['resident_id'], 'alice', {
                'operation': 'switch_account', 'target_account': 'one', 'context_strategy': 'compact',
            })
        assert operations == []


def test_replay_rejects_changed_request(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        payload = {'context_strategy': 'preserve', 'idempotency_key': 'same-id'}
        first = domain.restart_resident_session(record['resident_id'], 'alice', payload)
        assert domain.restart_resident_session(record['resident_id'], 'alice', payload) == first
        with pytest.raises(ValueError, match='different request'):
            domain.restart_resident_session(record['resident_id'], 'alice', {**payload, 'target_account': 'one'})
        assert operations == []




def test_cross_provider_requires_explicit_model_before_side_effects(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        original = copy.deepcopy(record)
        binding = copy.deepcopy(resident_backend.session_record(record))
        for model_field in ({}, {'model': None}, {'model': ''}, {'model': '   '}):
            with pytest.raises(ValueError, match='跨 Provider.*model'):
                domain.restart_resident_session(record['resident_id'], 'alice', {
                    'target_provider': 'claude', 'target_account': 'deepseek_primary',
                    'context_strategy': 'preserve', **model_field,
                })
            assert record == original and operations == [] and saved == []
            assert resident_backend.session_record(record) == binding




@pytest.mark.parametrize('has_session,previous_session', [(True, False), (False, True), (False, False)])
def test_switch_options_report_context_limits_without_reducing_context(monkeypatch, isolated_portal_case, has_session, previous_session):
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        if not has_session:
            monkeypatch.setattr(resident_backend, 'session_record', lambda *_: {})
        monkeypatch.setattr(execution_port, 'latest_provider_session_ref', lambda *a, **kw: 'unbound-history' if previous_session else '')
        monkeypatch.setattr(domain, 'runtime_configuration_catalog', lambda: {'backends': [
            {'provider': provider, 'routes': [{'backend_route_id': route, 'account_id': route, 'label': route}]}
            for provider, route in [('codex', 'one'), ('claude', 'deepseek_primary'), ('provider_api', 'api-one')]
        ]})
        options = domain.get_resident_restart_options(record['resident_id'], 'alice')
        codex = options['accounts']['codex'][0]['context_options']
        claude = {row['id']: row for row in options['accounts']['claude'][0]['context_options']}
        api = {row['id']: row for row in options['accounts']['provider_api'][0]['context_options']}
        assert [row['id'] for row in codex] == ['preserve']
        assert codex[0]['enabled']
        assert set(claude) == {'preserve'} and claude['preserve']['enabled']
        assert set(api) == {'preserve'} and api['preserve']['enabled']
        assert operations == [] and saved == [], 'reading choices never starts a session or discards context'






def test_codex_empty_create_imports_and_records_effective_context(tmp_path, isolated_portal_case):
    from dataclasses import replace
    from unittest import mock
    from management_portal.execution.backend_contracts import BackendContext
    from management_portal.execution.codex_backend import CodexBackend
    from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation

    with isolated_portal_case():
        agent_id = 'resident_preserving'
        store, expected = _effective_history(agent_id)
        prepared = execution_port.prepare_context_preservation(agent_id, actor='alice',
            operation_id='preserving', source_binding={})
        seed = execution_port.commit_context_preservation(prepared)
        request = replace(_turn_request('session-restart:preserving'), operation='create', metadata={
            'resident_id': agent_id, 'preserve_session_context': True, 'fork_context_input': seed,
        })
        session = _runtime_session(request, '', is_new=True)
        registry, client = mock.Mock(), mock.Mock()
        registry.get.return_value = mock.Mock(account_id='zero', socket_path=tmp_path / 'fake.sock')
        registry.candidates.return_value = [registry.get.return_value]
        registry.find_thread_rollout.return_value = None
        client.start_thread.return_value = {'thread': {'id': 'new-thread'}}
        client.run_turn.side_effect = AssertionError('must not run a model')
        backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
        context = BackendContext(emit_event=lambda *_: None, cancellation_requested=lambda: False,
                                 set_interrupt_target=lambda *_: None)
        result = backend.execute(_invocation(request, session), context)
        client.start_thread.assert_called_once()
        assert result.output['context_result'] == 'preserved_import'
        imports = [c for c in client.request.call_args_list if c.args[0] == 'thread/inject_items']
        assert len(imports) == 1
        assert imports[0].args[1]['items'][0]['content'][1]['type'] == 'input_image'
        assert imports[0].args[1]['items'][1]['call_id'] == 'original-call'
        snapshot = store.provider_context_snapshot(provider='codex', backend_route_id='zero',
                                                    backend_session_id='new-thread', agent_id=agent_id)
        assert [x['segment_id'] for x in snapshot['segments']] == expected
        backend.execute(_invocation(request, _runtime_session(request, 'new-thread')), context)
        assert len([c for c in client.request.call_args_list if c.args[0] == 'thread/inject_items']) == 1


def test_source_binding_changes_before_fork_preparation(isolated_portal_case):
    from management_portal.execution.contracts import ExecutionRequest, SessionOptions
    from management_portal.execution.session_manager import SessionManager

    with isolated_portal_case():
        sessions = SessionManager()
        sessions.bind_session('source', provider='codex', backend_session_id='before', backend_route_id='zero')
        source = sessions.provider_binding('source')
        request = ExecutionRequest.new(
            actor='alice', agent_id='resident_source', service_type='agent_session', provider='codex',
            operation='fork', prompt='preserve history', session=SessionOptions(mode='persistent', binding_id='candidate',
                                                    context_source_binding_id='source'),
            metadata={'expected_source_binding': source},
        )
        sessions.bind_session('replacement', provider='codex', backend_session_id='after', backend_route_id='zero')
        sessions.replace_session('source', 'replacement')
        with pytest.raises(RuntimeError, match='binding changed'):
            sessions.prepare(request)
        assert not sessions.provider_binding('candidate')
        assert sessions.provider_binding('source')['backend_session_id'] == 'after'


def test_preservation_fork_refuses_lossy_native_reasoning(tmp_path, isolated_portal_case):
    from dataclasses import replace
    from unittest import mock
    from management_portal.codex_app_server_protocol import CodexThreadNotFound
    from management_portal.execution.backend_contracts import BackendContext
    from management_portal.execution.codex_backend import CodexBackend
    from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation

    with isolated_portal_case():
        store = AgentDataStore()
        agent_id = 'resident_reasoning'
        source = store.ensure_provider_session(provider='codex', backend_route_id='zero',
                                              backend_session_id='source', agent_id=agent_id, actor_ref='alice')
        reasoning = store.put_segment(segment_type='model', content='partial reasoning transcript',
                                      metadata={'kind': 'reasoning'})
        store.append_agent_segments(agent_id, [reasoning], actor_ref='alice')
        store.record_context_received(agent_id, source['provider_session_ref'], [reasoning], actor_ref='alice')
        request = replace(_turn_request(agent_id), operation='fork', metadata={'preserve_session_context': True})
        client, registry = mock.Mock(), mock.Mock()
        registry.get.return_value = mock.Mock(account_id='zero', socket_path=tmp_path / 'fake.sock')
        registry.find_thread_rollout.return_value = None
        client.fork_thread.side_effect = CodexThreadNotFound('thread not found: source')
        backend = CodexBackend(account_registry=registry, client_factory=lambda _: client)
        context = BackendContext(emit_event=lambda *_: None, cancellation_requested=lambda: False,
                                 set_interrupt_target=lambda *_: None)
        with pytest.raises(ValueError, match='reasoning'):
            backend.execute(_invocation(request, _runtime_session(request, 'source')), context)
        client.start_thread.assert_not_called()
        client.run_turn.assert_not_called()


@pytest.mark.parametrize('content', [
    {'encrypted': True, 'summary': []},
    {'type': 'reasoning', 'content': [{'text': 'readable but cannot preserve native type'}]},
])
def test_preserve_rejects_incomplete_reasoning(content):
    from management_portal.execution.session_history_import import validate_codex_preservation
    with pytest.raises(ValueError, match='preserve'):
        validate_codex_preservation([{'segment_id': 'rs', 'segment_type': 'model',
                                     'content': content, 'metadata': {'kind': 'reasoning'}}])


def test_switch_waits_then_freezes_finished_turn(monkeypatch, isolated_portal_case):
    from management_portal.society_service import resident_execution_work, resident_deferred_restart
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        store, expected = _effective_history(record['resident_id'])
        work = {'status': 'running', 'execution_id': 'current'}
        monkeypatch.setattr(resident_execution_work, 'read', lambda *a: work)
        monkeypatch.setattr(resident_execution_work, 'is_active', lambda item: bool(item))
        payload = {'operation': 'switch_account', 'target_account': 'one', 'idempotency_key': 'drain'}
        first = domain.restart_resident_session(record['resident_id'], 'alice', payload)
        assert first['phase'] == 'waiting_current_execution' and not operations
        assert domain.restart_resident_session(record['resident_id'], 'alice', payload) == first
        last = store.put_segment(segment_type='model', content='final answer of running turn')
        store.append_agent_segments(record['resident_id'], [last], actor_ref='test')
        work.clear()
        assert resident_deferred_restart.advance(record) is False
        assert not operations
        assert record['session_restart']['status'] == 'completed'
        assert record['session_restart']['context_result'] == 'pending_first_input'
        assert [item['segment_id'] for item in store.agent_context_snapshot(record['resident_id'])['segments']] == expected + [last]


def test_cancel_waiting_switch_preserves_work_and_binding(monkeypatch, isolated_portal_case):
    from management_portal.society_service import resident_execution_work
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        work = {
            'status': 'running', 'execution_id': 'current', 'user_message_id': 'original',
            'submitted_at': '2026-10-27T00:00:00Z',
        }
        monkeypatch.setattr(resident_execution_work, 'read', lambda *a: work)
        monkeypatch.setattr(resident_execution_work, 'is_active', lambda item: bool(item))
        first = domain.restart_resident_session(record['resident_id'], 'alice', {
            'operation': 'switch_account', 'target_account': 'one', 'idempotency_key': 'cancel-wait',
        })
        assert first['phase'] == 'waiting_current_execution'
        cancelled = domain.cancel_resident_session_restart(record['resident_id'], 'alice', first['operation_id'])
        assert cancelled['status'] == 'cancelled' and cancelled['context_result'] == 'unchanged'
        assert work['user_message_id'] == 'original' and operations == []
        assert resident_backend.session_record(record)['backend_session_id'] == 'session_old'


def test_successful_switch_does_not_retry_failed_input(monkeypatch, isolated_portal_case):
    from management_portal.society_service import resident_work_recovery
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        _effective_history(record['resident_id'])
        monkeypatch.setattr(resident_work_recovery, 'latest_failed_input', lambda *a: pytest.fail('unrelated failed input'))
        monkeypatch.setattr(resident_work_recovery, 'resume_after_restart', lambda *a: pytest.fail('implicit retry'))
        domain.restart_resident_session(record['resident_id'], 'alice', {
            'operation': 'switch_account', 'target_account': 'one',
        })
        assert not operations
        assert record['session_restart']['status'] == 'completed'
        assert record['session_restart']['context_result'] == 'pending_first_input'




@pytest.mark.parametrize('provider,strategy,account', [
    ('codex', 'preserve', 'one'), ('codex', 'preserve', 'zero'),
    ('claude', 'preserve', 'deepseek_primary'), ('provider_api', 'preserve', 'test-api'),
])
@pytest.mark.parametrize('auto_switch_on_quota', [True, False])
def test_candidate_retains_platform_settings_and_freezes_revision(
    monkeypatch, isolated_portal_case, tmp_path, provider, strategy, account, auto_switch_on_quota,
):
    from management_portal.society_service import resident_runtime_domain, resident_record_domain
    from management_portal.execution.resident_capability_settings import normalize_capability_config, settings_revision
    from management_portal.execution.harness_settings import normalize_agent_harness_settings
    from management_portal.execution.harness_native_loop import NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID
    from tests.cases.test_session_restart import _model_api_restart

    with isolated_portal_case():
        runtime_options = domain._session_restart_runtime_options
        record, operations, _ = (_model_api_restart if provider == 'provider_api' else _restart_domain)(monkeypatch)
        monkeypatch.setattr(domain, '_session_restart_runtime_options', runtime_options)
        monkeypatch.setattr(resident_record_domain, 'sessions_path', lambda *_a: tmp_path)
        settings = resident_runtime_domain.read_resident_runtime_settings(record)
        shared = {
            'auto_switch_on_quota': auto_switch_on_quota,
            'capability_config': normalize_capability_config({
                'schema': 'tfi.resident-capabilities.v1',
                'tools': [{'ref': 'portal:hatg_instruction', 'required': True}],
            }),
            'prompt_injection': {'preset': 'pure'},
            'prompt_context': {'system_prompt': 'Keep this Portal instruction.', 'messages': []},
            'agent_harness': normalize_agent_harness_settings({
                'features': {NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID: {'enabled': False}},
            }),
        }
        settings.update(shared)
        monkeypatch.setattr(resident_runtime_domain, 'read_resident_runtime_settings', lambda *_a: copy.deepcopy(settings))
        domain.restart_resident_session(record['resident_id'], 'alice', {
            'target_provider': provider, 'target_account': account, 'context_strategy': strategy,
            **({'model': {'claude': 'deepseek-v4-pro', 'provider_api': 'api-model'}[provider]}
               if provider != 'codex' else {}),
        })
        if operations:
            options = operations[-1]['step']['request']['runtime_options']
            target = options['runtime_settings']
            for name, value in shared.items():
                assert target[name] == value
            assert options['resident_assembly']['settings_revision'] == settings_revision(
                target, backend=provider, backend_route_id=account,
            )
        else:
            assert record['execution_settings']['auto_switch_on_quota'] is auto_switch_on_quota
            for name in ('capability_config', 'prompt_injection', *(['prompt_context'] if provider == 'provider_api' else [])):
                assert record['execution_settings'][name].items() >= shared[name].items()
            assert record['harness_config'] == shared['agent_harness']

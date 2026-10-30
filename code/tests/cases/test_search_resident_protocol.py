from __future__ import annotations
from management_portal.society_service import resident_execution_work

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal import resident_handoff, shared_search_invocation as client
from management_portal.society_service import (
    invariant_locks, resident_domain, resident_input_scope, resident_protocol,
    service_invocation_domain as plans,
)
from management_portal.society_service import resident_intake_domain
from management_portal.society_service import resident_record_domain
from management_portal.society_service.lineage_storage import canonical_lineage_folder
from management_portal.society_service.principal_hierarchy import classify_existing
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.society_service.state_store import LineageDataStore
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.service_invocation_contract import batch_manifest

RID = 'resident_112233445566'
LID = 'lineage_112233445566'


from management_portal.society_service import resident_execution_domain

def job(actor='alice', generation=1):
    return dict(created_by=actor, surface='knowledge', search_id='search_112233445566', run_generation=generation)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    store = LineageDataStore()
    repo = ResidentConversationRepository(store, AgentDataStore())
    lineage = {"lineage_id": LID, "nodes": [], "edges": []}
    record = dict(resident_id=RID, lineage_id=LID, title='Search fixture', backend='provider_api', folder_schema='lineage-folder-v3',
                  workspace_path=str(canonical_lineage_folder(LID)),
                  backend_route_id='test', execution_settings={'model':'ranker', 'prompt_context':{'system_prompt':'Rank candidates'}})
    store.put_lineage_definition(LID, lineage, {})
    record['principal_hierarchy'] = classify_existing(record, lineage, evidence={'scope_ids': [LID]})
    resident_record_domain.set_resident_owned_workspace_binding(record)
    repo.agent_data.ensure_agent(RID, actor_ref='fixture')
    store.put_resident_record(RID, record, commit_configuration=True)
    record = store.resident_record(RID)
    monkeypatch.setattr(invariant_locks, '_ROOT_PROVIDER', lambda: tmp_path)
    monkeypatch.setattr(resident_input_scope, 'get_lineage_data_store', lambda: store)
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, 'get_repository', lambda: repo)
    access = SimpleNamespace(is_active=lambda actor: actor in {'alice', 'bob'})
    monkeypatch.setattr(resident_input_scope, 'get_portal_access_store', lambda: access)
    monkeypatch.setattr(plans, 'get_portal_access_store', lambda: access)
    binding = dict(resident_id=RID, lineage_id=LID, revision=1, surface='knowledge')
    monkeypatch.setattr(plans, 'search_binding', lambda surface: copy.deepcopy(binding))
    monkeypatch.setattr(plans, '_authorize_new', lambda *args: {})

    def target(resident_id, actor='', require_visible=False):
        assert resident_id == RID
        if require_visible:
            raise PermissionError('shared Resident history is private')
        return resident_domain.ResidentRuntimeTarget(RID, record)
    monkeypatch.setattr(resident_record_domain, 'resolve_resident_runtime_target', target)
    monkeypatch.setattr(resident_record_domain, 'resolve_resident_admission_target', target)
    monkeypatch.setattr(resident_protocol, '_resident_view', lambda target: {'resident_id': RID, 'lineage_id': LID})
    monkeypatch.setattr(resident_protocol.resident_access, 'can_manage_resident', lambda *args, **kwargs: False)
    monkeypatch.setattr(resident_handoff, 'ensure_notification_listener', lambda: None)
    monkeypatch.setattr(resident_handoff, 'get_notification_hub', lambda: SimpleNamespace(sequence=0))
    submitted = []
    lose_ack = [False]
    def deliver(*, actor, identity, message, **options):
        assert resident_input_scope.permits(RID, actor, message['message_id'])
        with invariant_locks.resident_lock(RID):
            if repo.message(RID, message['message_id']) is None:
                submitted.append(message['message_id'])
                user = {**message, 'author_id': actor, 'created_at': '2026-09-25T00:00:00Z'}
                repo.commit_user_message(RID, user)
                reply_id = 'msg_' + hashlib.sha256(message['message_id'].encode()).hexdigest()[:32]
                repo.append_message(RID, dict(message_id=reply_id, role='agent', reply_to_message_id=message['message_id'],
                                             created_at='2026-09-25T00:00:01Z', content_parts=message['content_parts']))
                repo.set_input_state(RID, message['message_id'], 'completed')
        if lose_ack[0]:
            lose_ack[0] = False
            raise ConnectionError('accepted, acknowledgement lost')
        return {'input_state': repo.input_state(RID, message['message_id'])}
    monkeypatch.setattr(plans, 'deliver_resident_message', deliver)
    service = SimpleNamespace(service_invocation=plans, resident=resident_protocol)
    monkeypatch.setattr(client, 'get_society_service', lambda: service)
    monkeypatch.setattr(resident_handoff, 'get_society_service', lambda: service)
    return SimpleNamespace(repo=repo, record=record, binding=binding, submitted=submitted, lose_ack=lose_ack)


def run(actor='alice', generation=1, prompt='private candidates', **kwargs):
    return client.run_shared_search_turn(job=job(actor, generation), surface='knowledge', prompt=prompt, **kwargs)


def test_search_uses_standard_message_input_reply_and_events(runtime):
    """共享检索通过标准的消息输入、回复和会话事件与 Resident 交互"""
    result = run()
    mid = result['message_id']
    state = resident_protocol.get_resident_input_state(RID, 'alice', mid)
    assert state['state'] == 'completed' and state['reply_message_ids']
    assert result['message'] == 'private candidates'
    assert runtime.repo.message(RID, mid)['author_id'] == 'alice'
    assert {event['event_type'] for event in runtime.repo.events_after(RID, 0)['events']} >= {
        'resident.input.state.changed', 'conversation.message.appended',
    }
    assert 'execution_id' not in result


def test_two_users_share_resident_but_never_input_context_or_results(runtime):
    """两个用户共用检索 Resident，但互相看不到对方的输入、上下文和结果"""
    a, b = run('alice', prompt='alice private'), run('bob', prompt='bob private')
    assert a['resident_id'] == b['resident_id'] == RID
    assert a['message_id'] != b['message_id']
    for owner, other, result in [('alice', 'bob', a), ('bob', 'alice', b)]:
        snapshot = resident_protocol.get_resident_conversation_snapshot(RID, owner, input_message_id=result['message_id'])
        assert all(other not in str(message) for message in snapshot['messages'])
        events = resident_protocol.get_resident_events(RID, owner, 0, input_message_id=result['message_id'])
        assert all(other + ' private' not in str(event) for event in events['events'])
        assert [event['cursor'] for event in events['events']] == list(range(1, events['cursor'] + 1))
        with pytest.raises(PermissionError):
            resident_protocol.get_resident_input_state(RID, other, result['message_id'])
        with pytest.raises(PermissionError):
            resident_protocol.get_resident_message(RID, other, result['messages'][0]['message_id'])
        with pytest.raises(PermissionError):
            resident_protocol.cancel_resident_input(RID, other, result['message_id'])
        with pytest.raises(PermissionError):
            resident_protocol.get_resident_conversation_snapshot(RID, owner)


def test_concurrent_retry_and_lost_ack_publish_only_one_input(runtime):
    """确认丢失后并发重试检索只投递一条输入"""
    runtime.lose_ack[0] = True
    with pytest.raises(ConnectionError):
        run()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(), range(4)))
    assert len(set(result['message_id'] for result in results)) == 1
    assert len(runtime.submitted) == 1


def test_scoped_snapshot_keeps_intermediate_replies_without_other_inputs(runtime):
    """单输入快照在运行中保留中间消息，结束后仍按身份保留同文回复。"""
    repo = runtime.repo
    mid = 'msg_live_scope'
    resident_input_scope.register(resident_id=RID, message_id=mid, actor='alice', profile={})
    repo.commit_user_message(RID, dict(message_id=mid, role='user', author_id='alice',
                                     created_at='2026-10-22T00:00:00Z',
                                     content_parts=[{'type': 'text', 'text': 'alice task'}]))
    repo.set_input_state(RID, mid, 'running')

    def reply(identity, text, at):
        repo.append_message(RID, dict(message_id=identity, role='agent', reply_to_message_id=mid,
                                     created_at=at, content_parts=[{'type': 'text', 'text': text}]))

    reply('msg_live_first', 'same text', '2026-10-22T00:00:01Z')
    # A newer unrelated turn must not hide or leak into this input's snapshot.
    run('bob', prompt='bob private')
    snapshot = resident_protocol.get_resident_conversation_snapshot(
        RID, 'alice', input_message_id=mid, history_limit=1)
    assert [m['message_id'] for m in snapshot['messages']] == [mid, 'msg_live_first']
    assert snapshot['input_states'][0]['state'] == 'running'
    assert snapshot['input_states'][0]['reply_message_ids'] == []
    assert 'bob private' not in str(snapshot['messages'])

    reply('msg_live_second', 'same text', '2026-10-22T00:00:02Z')
    reply('msg_live_final', 'final text', '2026-10-22T00:00:03Z')
    repo.set_input_state(RID, mid, 'completed')
    snapshot = resident_protocol.get_resident_conversation_snapshot(RID, 'alice', input_message_id=mid)
    assert [m['message_id'] for m in snapshot['messages']] == [
        mid, 'msg_live_first', 'msg_live_second', 'msg_live_final']
    assert snapshot['input_states'][0]['reply_message_ids'] == ['msg_live_final']
    assert 'bob private' not in str(snapshot['messages'])
    limited = resident_protocol.get_resident_conversation_snapshot(
        RID, 'alice', input_message_id=mid, history_limit=1)
    assert [m['message_id'] for m in limited['messages']] == [mid, 'msg_live_final']
    with pytest.raises(PermissionError):
        resident_protocol.get_resident_conversation_snapshot(RID, 'bob', input_message_id=mid)


def test_provider_resident_receives_only_current_input_from_its_own_configuration(runtime):
    """Provider Resident 只收到本次检索输入和自身配置的系统提示词"""
    a = run('alice', prompt='current query')
    run('bob', prompt='unrelated secret')
    request = resident_execution_domain._build_resident_execution_request(runtime.record, dict(
        actor='alice', user_message_id=a['message_id'], job_id='job-one',
        raw_message='current query', actual_sent_prompt='must never reach provider',
    ))
    assert request.agent_id == RID and request.actor == 'alice'
    assert request.prompt == 'current query'
    assert request.runtime_options['messages'] == [{'role': 'user', 'content': 'current query'}]
    assert request.provider == 'test'
    assert [part.content for part in request.system_instructions.parts if part.name == 'business_instructions'] == ['Rank candidates']


def test_refresh_fences_late_previous_result(runtime, monkeypatch):
    """刷新检索后，上一代迟到的结果被拒收"""
    original = client.resume_and_wait
    def late(**kwargs):
        result = original(**kwargs)
        plans.prepare_batches(job=job(generation=2), surface='knowledge', batches=batch_manifest(job(generation=2), ['new query']))
        return result
    monkeypatch.setattr(client, 'resume_and_wait', late)
    with pytest.raises(client.SharedSearchInvocationFailed):
        run()
    monkeypatch.setattr(client, 'resume_and_wait', original)
    assert run(generation=2, prompt='new query')['message'] == 'new query'


def test_cancel_before_prepare_fences_only_same_user(runtime):
    """检索在准备前被取消只阻止同一用户的这次运行，不影响其他用户"""
    plans.cancel_invocation(job=job(), surface='knowledge')
    with pytest.raises(client.SharedSearchInvocationConflict):
        run()
    assert run('bob')['message'] == 'private candidates'


def test_scope_is_immutable_and_payload_cannot_override_authorized_input(runtime):
    """检索输入的授权范围登记后不可更改，也不能用其他内容覆盖已授权的输入"""
    result = run()
    profile = resident_input_scope.get(RID, result['message_id'])['profile']
    with pytest.raises(ValueError, match='immutable'):
        resident_input_scope.register(resident_id=RID, message_id=result['message_id'], actor='bob', profile=profile)
    with pytest.raises(ValueError, match='authorized invocation'):
        resident_intake_domain.persist_resident_input(RID, 'alice', 'changed prompt', message_id=result['message_id'])


# The actual intake/queue owner is exercised separately from the provider simulator.
from tests.cases.resident_execution_test_support import resident_record


def test_isolated_input_uses_bound_codex_resident_account_and_model(resident_record, monkeypatch):
    """隔离的检索输入使用 codex Resident 绑定的账号和模型，以无状态会话运行"""
    from management_portal.society_service import resident_runtime_domain

    resident_id = resident_record['resident_id']
    message_id = 'msg_' + 'dcba' * 8
    monkeypatch.setattr(resident_input_scope, 'get_portal_access_store',
                        lambda: SimpleNamespace(is_active=lambda actor: actor == 'CASE-A'))
    resident_input_scope.register(resident_id=resident_id, message_id=message_id, actor='CASE-A',
                                  profile={'input_sha256': hashlib.sha256(b'find relevant papers').hexdigest()})
    settings = resident_runtime_domain.read_resident_runtime_settings(resident_record)
    resident_runtime_domain.write_resident_runtime_settings(resident_record, {
        **settings, 'model': 'gpt-6-luna', 'reasoning_effort': 'max',
    })
    request = resident_execution_domain._build_resident_execution_request(resident_record, {
        'actor': 'CASE-A', 'execution_id': 'job_scoped_codex', 'user_message_id': message_id,
        'raw_message': 'find relevant papers', 'actual_sent_prompt': 'unrelated history',
    })
    assert request.provider == 'codex'
    assert request.prompt.startswith('find relevant papers')
    assert 'unrelated history' not in request.prompt
    assert request.session.mode == 'stateless' and not request.session.binding_id
    assert request.session.backend_route_id == 'zero'
    assert request.runtime_options['session_key'] == ''
    assert request.runtime_options['runtime_settings']['model'] == 'gpt-6-luna'
    assert request.runtime_options['runtime_settings']['reasoning_effort'] == 'max'


def test_isolated_search_enters_real_resident_queue(resident_record, monkeypatch):
    """隔离的检索输入进入 Resident 的真实队列，只携带本次输入"""
    rid = resident_record['resident_id']
    mid = 'msg_' + 'abcd' * 8
    profile = dict(input_sha256=hashlib.sha256(b'current candidates').hexdigest())
    monkeypatch.setattr(resident_input_scope, 'get_portal_access_store',
                        lambda: SimpleNamespace(is_active=lambda actor: actor == 'alice'))
    resident_input_scope.register(resident_id=rid, message_id=mid, actor='alice', profile=profile)
    from management_portal.society_service.server import LocalSocietyService
    monkeypatch.setattr(resident_handoff, 'get_society_service', LocalSocietyService)
    result = resident_handoff.deliver_resident_message(actor='alice', identity=resident_record, message={
        'message_id': mid, 'role': 'user', 'content_parts': [{'type': 'text', 'text': 'current candidates'}],
    })
    assert result['message_id'] == mid
    assert resident_protocol.get_resident_input_state(rid, 'alice', mid)['state'] in {'queued', 'running'}
    current = resident_execution_work.read(resident_record)
    assert current['user_message_id'] == mid and current['actor'] == 'alice'
    assert current['effective_message'] == 'current candidates'
    assert 'actual_sent_prompt' not in current


def _pending_delivery(runtime, monkeypatch):
    def deliver(*, actor, identity, message, **options):
        mid = message['message_id']
        assert resident_input_scope.permits(RID, actor, mid)
        runtime.submitted.append(mid)
        runtime.repo.commit_user_message(RID, {**message, 'author_id': actor, 'created_at': '2026-09-25T00:00:00Z'})
        runtime.repo.set_input_state(RID, mid, 'queued')
        return {'input_state': runtime.repo.input_state(RID, mid)}
    monkeypatch.setattr(plans, 'deliver_resident_message', deliver)


def _finish(runtime, mid):
    runtime.repo.append_message(RID, dict(message_id='msg_finished', role='agent', reply_to_message_id=mid,
        created_at='2026-09-25T00:00:02Z', content_parts=[{'type': 'text', 'text': 'ranked results'}]))
    runtime.repo.set_input_state(RID, mid, 'completed')


@pytest.mark.parametrize('fault', ['event', 'reconnected', 'notification_gap', 'cursor_gap'])
def test_search_resumes_standard_events_after_disconnect(runtime, monkeypatch, fault):
    """通知断线、重连或游标缺口后，检索从标准事件恢复并拿到结果，不重复投递"""
    _pending_delivery(runtime, monkeypatch)
    calls = []
    class Hub:
        sequence = 0
        def cursor_has_gap(self, cursor):
            return fault == 'notification_gap'
        def wait(self, cursor, **kwargs):
            calls.append(cursor)
            assert len(calls) == 1
            _finish(runtime, runtime.submitted[0])
            subject = 'portal.gateway.reconnected' if fault == 'reconnected' else f'portal.resident.changed.{RID}.conversation'
            return [SimpleNamespace(subject=subject)], cursor + 1
    monkeypatch.setattr(resident_handoff, 'get_notification_hub', Hub)
    if fault == 'cursor_gap':
        monkeypatch.setattr(resident_handoff, '_events_after', lambda *a, **k: (0, [], True))
    result = run()
    assert result['message'] == 'ranked results'
    assert len(runtime.submitted) == 1
    assert runtime.repo.input_state(RID, result['message_id'])['reply_message_ids'] == ['msg_finished']


def test_observation_timeout_keeps_input_and_late_reply_is_reused(runtime, monkeypatch):
    """等待检索结果超时后保留已投递的输入，稍后再次请求直接复用迟到的回复"""
    _pending_delivery(runtime, monkeypatch)
    with pytest.raises(client.SharedSearchObservationPending) as pending:
        run(timeout_seconds=0.000001)
    mid = pending.value.message_id
    assert pending.value.input_state['state'] == 'queued'
    assert len(runtime.submitted) == 1
    _finish(runtime, mid)
    result = run()
    assert result['message_id'] == mid and result['message'] == 'ranked results'
    assert len(runtime.submitted) == 1


def test_isolated_input_does_not_consume_shared_context(runtime, monkeypatch):
    """隔离的检索输入不检查也不消耗 Resident 的共享上下文"""
    result = run()
    record = dict(runtime.record)
    job_record = {'user_message_id': result['message_id'], 'raw_message': 'current candidates'}
    monkeypatch.setattr(resident_execution_domain, '_requires_initial_context_compaction',
                        lambda record: pytest.fail('isolated input inspected shared context'))
    assert resident_execution_domain._portable_context_execution(record, job_record) is job_record

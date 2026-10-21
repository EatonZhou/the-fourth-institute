from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import (
    board_semantic_search_backend, paper_backend, resident_handoff, semantic_search_jobs, semantic_search_store,
    shared_search_adapter as adapter, shared_search_invocation,
)
from knowledge_board_lan import knowledge_backend
from management_portal.semantic_search_runs import SearchRunSuperseded


SURFACES = ['knowledge', 'skill', 'repo', 'article', 'experiment', 'paper', 'wall']


@pytest.fixture(params=SURFACES)
def surface_jobs(request, tmp_path, monkeypatch):
    return make_surface_jobs(request.param, tmp_path, monkeypatch)


def make_surface_jobs(surface, tmp_path, monkeypatch):
    getter = lambda search_id: semantic_search_jobs.search_by_id(surface, search_id)
    setter = lambda search_id, patch, **kwargs: semantic_search_jobs.set_search(surface, search_id, patch, **kwargs)
    monkeypatch.setattr(semantic_search_jobs, 'publish_local_projection_change', Mock())
    monkeypatch.setattr(semantic_search_jobs, 'start_task', lambda job: job)
    # The WALL project check reads WALL itself; the fixture project stands for an open one.
    monkeypatch.setattr(semantic_search_jobs, '_project',
                        lambda declared, project, actor: str(project or '') if declared.project else '')
    board = surface == 'wall'
    start = lambda actor: semantic_search_jobs.start_search(
        surface, {'query': actor + ' query', **({'project': 'fixture'} if board else {})}, actor=actor)
    key = adapter.result_key(surface)
    identifier = ('decision' if surface == 'knowledge' else surface) + '_0123456789ab'
    candidates = {
        'alice': [{key: identifier, 'resource_type': surface,
                   'title': 'alice candidate', 'preview': 'alice private preview', 'summary': 'alice private summary',
                   'path': '/forbidden/file', 'allowed_paths': ['/forbidden'], 'private_history': 'never send history',
                   'attributes': {'owner_ids': ['alice'], 'secret': 'never send raw attrs'}, 'source': {'path': '/forbidden'}}],
        'bob': [{key: identifier, 'resource_type': surface,
                 'title': 'bob candidate', 'summary': 'bob private summary'}],
    }
    reads = []
    def records(actor):
        reads.append(actor)
        return candidates[actor]
    if board:
        monkeypatch.setattr(board_semantic_search_backend, 'search_catalog', lambda job: records(job['created_by']))
    elif surface == 'paper':
        monkeypatch.setattr(paper_backend, 'resource_index_papers', lambda *, actor: records(actor))
    elif surface == 'knowledge':
        monkeypatch.setattr(knowledge_backend, 'resource_index_snapshot', lambda *, actor, active_only: {'items': records(actor)})
    else:
        service = Mock()
        service.query.side_effect = lambda *, actor, **kwargs: {'resources': records(actor)}
        monkeypatch.setattr('management_portal.resources.get_resource_service', lambda: service)
    def create(actor='alice'):
        result = start(actor)
        return setter(result['search_id'], {'execution_contract': adapter.CONTRACT})
    plans = {}
    submitted = []
    def identity(job):
        return tuple(job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation'))
    def prepare(*, job, surface, prompts):
        plan_key = identity(job)
        fingerprint = hashlib.sha256(json.dumps([plan_key, prompts]).encode()).hexdigest()
        if plan_key in plans:
            assert plans[plan_key]['plan_fingerprint'] == fingerprint
            return copy.deepcopy(plans[plan_key])
        receipt = {field: job[field] for field in ('search_id', 'surface', 'created_by', 'run_generation')}
        receipt.update({'resident_id': 'resident_0123456789ab', 'lineage_id': 'lineage_0123456789ab',
                        'binding': {'revision': 7}, 'plan_fingerprint': fingerprint, 'status': 'prepared',
                        'fenced': False, 'is_current_generation': True})
        batches = [{**receipt, 'batch_id': f'batch-{index}',
                    'input_fingerprint': hashlib.sha256(prompt.encode()).hexdigest(),
                    'message_id': f'execution_{fingerprint[:12]}_{index}',
                    'resident_id': f'agent_{fingerprint[:12]}_{index}'} for index, prompt in enumerate(prompts)]
        receipt.update({'batches': batches, 'batch_count': len(batches)})
        if len(batches) == 1:
            receipt.update({field: batches[0][field] for field in ('message_id', 'resident_id')})
        plans[plan_key] = receipt
        return copy.deepcopy(receipt)
    def observe(*, job, surface):
        if identity(job) not in plans:
            raise KeyError('no receipt')
        return {'receipt': copy.deepcopy(plans[identity(job)]), 'execution': {}}
    def completion(**kwargs):
        job = kwargs['job']
        parent = plans[identity(job)]
        receipt = next(batch for batch in parent['batches'] if batch['batch_id'] == kwargs['batch_id'])
        assert kwargs['plan_fingerprint'] == receipt['plan_fingerprint']
        assert hashlib.sha256(kwargs['prompt'].encode()).hexdigest() == receipt['input_fingerprint']
        kwargs['on_assigned'](copy.deepcopy(receipt))
        assert not kwargs['cancellation_requested']()
        if receipt['status'] != 'completed':
            submitted.append(receipt['message_id'])
        receipt['status'] = 'completed'
        parent['status'] = 'completed' if all(batch['status'] == 'completed' for batch in parent['batches']) else 'running'
        return {'message': json.dumps({'results': [{key: identifier, 'reason': 'authorized reason', 'score': 91},
                                                   {key: 'never-authorized', 'reason': 'discard', 'score': 100}], 'notes': 'fixture notes'}),
                'receipt': copy.deepcopy(receipt), 'run_generation': job['run_generation'],
                'batch_id': receipt['batch_id'], 'plan_fingerprint': receipt['plan_fingerprint'],
                'message_id': receipt['message_id']}
    invoke = Mock(side_effect=completion)
    prepare_mock = Mock(side_effect=prepare)
    observe_mock = Mock(side_effect=observe)
    monkeypatch.setattr(shared_search_invocation, 'prepare_shared_search_batches', prepare_mock)
    monkeypatch.setattr(shared_search_invocation, 'run_shared_search_batch', invoke)
    monkeypatch.setattr(resident_handoff, 'run_shared_search_turn', Mock(side_effect=AssertionError('adapter must prepare the complete plan')))
    monkeypatch.setattr(resident_handoff, 'run_resident_turn', Mock(side_effect=AssertionError('legacy invocation is forbidden')))
    monkeypatch.setattr(shared_search_invocation, 'get_shared_search_invocation', observe_mock)
    monkeypatch.setattr(shared_search_invocation, 'read_shared_search_plan',
                        lambda *, job, surface: copy.deepcopy(plans.get(identity(job))))
    def cancel_parent(*, job, surface, deleted=False):
        parent = plans.get(identity(job))
        if parent:
            parent.update({'fenced': True, 'status': 'cancelled'})
        return parent
    cancel = Mock(side_effect=cancel_parent)
    monkeypatch.setattr(shared_search_invocation, 'cancel_shared_search_invocation', cancel)
    def fail_parent(*, job, surface, plan_fingerprint=''):
        parent = plans.get(identity(job))
        if parent:
            assert not plan_fingerprint or parent['plan_fingerprint'] == plan_fingerprint
            parent.update({'fenced': True, 'status': 'failed', 'fence_reason': 'failed'})
        return parent
    fail = Mock(side_effect=fail_parent)
    monkeypatch.setattr(shared_search_invocation, 'fail_shared_search_invocation', fail)
    return SimpleNamespace(surface=surface, getter=getter, setter=setter, create=create,
                           invoke=invoke, completion=completion, cancel=cancel, fail=fail, reads=reads, candidates=candidates,
                           key=key, identifier=identifier, tmp=tmp_path, plans=plans, submitted=submitted,
                           prepare=prepare_mock, observe=observe_mock)


def test_every_surface_uses_authoritative_actor_inline_candidates_and_receipt(surface_jobs):
    """各检索页面按创建者读取候选并保存冻结输入与回执"""
    jobs = surface_jobs
    first = jobs.create()
    adapter.run_search_job(first['search_id'], jobs.surface, 1)
    assert jobs.reads == ['alice', 'alice', 'alice']
    call = jobs.invoke.call_args.kwargs
    assert call['job']['created_by'] == 'alice'
    assert call['surface'] == jobs.surface
    assert call['job']['run_generation'] == 1
    assert 'alice private' in call['prompt']
    for forbidden in ['/forbidden', 'never send', 'bob private', 'allowed_paths', 'owner_ids']:
        assert forbidden not in call['prompt']
    current = jobs.getter(first['search_id'])
    assert current['status'] == 'done'
    assert len(current['results']) == 1
    receipt = shared_search_invocation.read_shared_search_plan(job=current, surface=jobs.surface)
    assert 'service_invocation' not in current
    assert receipt['created_by'] == 'alice'
    assert current['message_id'] == receipt['message_id']
    frozen = semantic_search_store.stored_run_input(first['search_id'])
    assert frozen['identity'] == {'search_id': first['search_id'], 'created_by': 'alice',
                                  'surface': jobs.surface, 'run_generation': 1}


def test_same_page_two_actors_never_share_candidate_prompt_or_run_identity(surface_jobs):
    """同页不同用户的检索候选提示词与运行身份隔离"""
    jobs = surface_jobs
    alice = jobs.create('alice')
    bob = jobs.create('bob')
    adapter.run_search_job(alice['search_id'], jobs.surface, 1)
    adapter.run_search_job(bob['search_id'], jobs.surface, 1)
    first, second = [call.kwargs for call in jobs.invoke.call_args_list]
    assert first['job']['created_by'] == 'alice' and second['job']['created_by'] == 'bob'
    assert first['job']['search_id'] != second['job']['search_id']
    assert 'bob private' not in first['prompt'] and 'alice private' not in second['prompt']


def test_result_write_rechecks_current_resource_access(surface_jobs):
    """检索结果写回前重新检查当前资源访问权限"""
    jobs = surface_jobs
    job = jobs.create()
    def revoke(**kwargs):
        result = jobs.completion(**kwargs)
        jobs.candidates['alice'] = []
        return result
    jobs.invoke.side_effect = revoke
    adapter.run_search_job(job['search_id'], jobs.surface, 1)
    assert jobs.getter(job['search_id'])['results'] == []


def test_deleted_during_invocation_fences_result_and_uses_original_generation(surface_jobs):
    """调用期间删除检索阻止结果写回并取消原代次"""
    jobs = surface_jobs
    job = jobs.create()
    def remove(**kwargs):
        result = jobs.completion(**kwargs)
        jobs.setter(job['search_id'], {'status': 'deleted', 'deleted': True})
        assert kwargs['cancellation_requested']()
        return result
    jobs.invoke.side_effect = remove
    adapter.run_search_job(job['search_id'], jobs.surface, 1)
    assert jobs.getter(job['search_id'])['status'] == 'deleted'
    assert jobs.getter(job['search_id'])['results'] == []
    assert jobs.cancel.call_args.kwargs['job']['run_generation'] == 1
    assert jobs.cancel.call_args.kwargs['deleted'] is True


def test_invalid_generation_or_missing_contract_never_invokes(surface_jobs):
    """检索代次无效或缺少执行契约时不发起调用"""
    jobs = surface_jobs
    job = jobs.create()
    with pytest.raises(SearchRunSuperseded):
        adapter.run_search_job(job['search_id'], jobs.surface, 2)
    jobs.setter(job['search_id'], {'execution_contract': ''})
    with pytest.raises(ValueError, match='contract'):
        adapter.run_search_job(job['search_id'], jobs.surface, 1)
    jobs.invoke.assert_not_called()


def test_large_knowledge_catalog_is_submitted_without_omitting_candidates():
    """大型知识候选目录完整写入检索提示词"""
    job = {'surface': 'knowledge', 'query': 'test'}
    catalog = [{'kb_id': f'decision_{index:012x}', 'summary': 'data' * 1000} for index in range(150)]
    prompt = adapter.candidate_prompt(job, catalog)
    assert json.loads(prompt.split('\n', 1)[1])['candidates'] == catalog


@pytest.mark.parametrize('surface_jobs', ['paper'], indirect=True)
def test_paper_refresh_keeps_authorized_previous_results_and_skips_recomparison(surface_jobs):
    """论文刷新保留仍获授权的旧结果且跳过重复比较"""
    jobs = surface_jobs
    job = jobs.create()
    jobs.setter(job['search_id'], {
        'refresh_skip_paper_ids': [jobs.identifier],
        'results': [{'paper_id': jobs.identifier, 'score': 83, 'reason': 'prior authorized match'}],
    })
    adapter.run_search_job(job['search_id'], 'paper', 1)
    assert jobs.identifier not in jobs.invoke.call_args.kwargs['prompt']
    results = jobs.getter(job['search_id'])['results']
    assert len(results) == 1 and results[0]['reason'] == 'prior authorized match'


def test_same_generation_reuses_frozen_input_when_catalog_gains_items(surface_jobs):
    """同代次重跑在目录新增候选后仍复用冻结输入"""
    jobs = surface_jobs
    job = jobs.create()
    adapter.run_search_job(job['search_id'], jobs.surface, 1)
    jobs.setter(job['search_id'], {'status': 'running'})
    added_id = jobs.identifier.replace('0123456789ab', '1123456789ab')
    jobs.candidates['alice'].append({jobs.key: added_id,
                                     'resource_type': jobs.surface, 'title': 'new visible candidate'})
    adapter.run_search_job(job['search_id'], jobs.surface, 1)
    first, second = [call.kwargs for call in jobs.invoke.call_args_list]
    assert first['prompt'] == second['prompt']
    assert first['job']['run_generation'] == second['job']['run_generation'] == 1


def test_page_worker_routes_new_runs_through_system_contract(surface_jobs):
    """页面工作线程通过系统契约执行新检索"""
    jobs = surface_jobs
    job = jobs.create()
    semantic_search_jobs.run_job(jobs.surface, job['search_id'], 1)
    jobs.invoke.assert_called_once()
    assert jobs.getter(job['search_id'])['execution_contract'] == adapter.CONTRACT
    assert jobs.getter(job['search_id'])['status'] == 'done'


def test_missing_duty_holder_errors_without_legacy_fallback(surface_jobs):
    """未登记检索职责承担者时记录错误且不回退旧调用"""
    from management_portal.application_targets import ApplicationUnconfigured
    jobs = surface_jobs
    job = jobs.create()
    jobs.invoke.side_effect = ApplicationUnconfigured('Portal 职责 search.knowledge 尚未登记承担者 (not configured)')
    with pytest.raises(ApplicationUnconfigured):
        semantic_search_jobs.run_job(jobs.surface, job['search_id'], 1)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'error'
    assert 'not configured' in current['error']
    assert current['created_by'] == 'alice'


def test_failure_finalization_is_distinct_from_explicit_cancellation(surface_jobs, subtests):
    """共享检索失败收尾与显式取消隔离

    验证各检索页面使用失败收尾保留真实错误，区分失败、取消、删除、换代与未知fence，并安全处理prepare之前和响应丢失的失败。

    :importance: P1
    :aspects: functional, behavior
    """
    jobs = surface_jobs
    scenarios = [
        ('provider_failure', 'failed', False, '', True, 'error', True),
        ('failed_fence', 'failed', True, 'failed', True, 'error', False),
        ('unknown_fence', 'prepared', True, 'unknown', True, 'error', False),
        ('cancelled', 'cancelled', True, 'cancelled', True, 'cancelled', False),
        ('deleted', 'prepared', True, 'deleted', True, 'cancelled', False),
        ('superseded', 'prepared', True, 'superseded', True, 'cancelled', False),
        ('foreign_receipt', 'cancelled', True, 'cancelled', False, 'error', True),
    ]
    for scenario, invocation_status, fenced, reason, trusted, expected_status, should_fail in scenarios:
        with subtests.test(scenario=scenario):
            job = jobs.create()
            jobs.cancel.reset_mock()
            jobs.fail.reset_mock()
            def reject(**arguments):
                receipt = {field: job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation')}
                receipt.update(status=invocation_status, fenced=fenced, fence_reason=reason, is_current_generation=True)
                if not trusted:
                    receipt['created_by'] = 'bob'
                raise shared_search_invocation.SharedSearchInvocationFailed('opaque failure', receipt=receipt)
            jobs.invoke.side_effect = reject
            with pytest.raises(shared_search_invocation.SharedSearchInvocationFailed, match='opaque failure'):
                adapter.run_search_job(job['search_id'], jobs.surface, 1)
            current = jobs.getter(job['search_id'])
            assert current['status'] == expected_status
            assert current['results'] == []
            jobs.cancel.assert_not_called()
            assert jobs.fail.call_count == int(should_fail)
            if should_fail:
                assert jobs.fail.call_args.kwargs['job']['search_id'] == job['search_id']
                assert jobs.fail.call_args.kwargs['plan_fingerprint']
            if trusted:
                assert current['input_failure']['fence_reason'] == reason
    prepare = jobs.prepare.side_effect
    for create_plan in (False, True):
        with subtests.test(prepare_response_lost=create_plan):
            job = jobs.create()
            jobs.cancel.reset_mock()
            jobs.fail.reset_mock()
            jobs.invoke.reset_mock()
            def unavailable(**arguments):
                if create_plan:
                    prepare(**arguments)
                raise ConnectionError('prepare response unavailable')
            jobs.prepare.side_effect = unavailable
            with pytest.raises(ConnectionError, match='prepare response unavailable'):
                adapter.run_search_job(job['search_id'], jobs.surface, 1)
            assert jobs.getter(job['search_id'])['status'] == 'error'
            jobs.invoke.assert_not_called()
            jobs.cancel.assert_not_called()
            jobs.fail.assert_called_once()
            assert jobs.fail.call_args.kwargs['plan_fingerprint'] == ''
    jobs.prepare.side_effect = prepare


def test_delete_before_assignment_fences_search_generation(surface_jobs):
    """检索分配执行前删除也会封锁当前代次"""
    jobs = surface_jobs
    job = jobs.create()
    _delete(jobs, job['search_id'])
    jobs.cancel.assert_called_once()
    assert jobs.cancel.call_args.kwargs['deleted'] is True
    assert jobs.cancel.call_args.kwargs['job']['run_generation'] == 1
    assert jobs.getter(job['search_id'])['deleted'] is True



def _delete(jobs, search_id):
    semantic_search_jobs.delete_search(jobs.surface, {'search_id': search_id, 'project': 'fixture'}, actor='alice')


def test_deleting_a_search_removes_its_frozen_input(surface_jobs):
    """删除检索同时删除冻结输入

    各检索页面删除 Saved Search 后，其 semantic_search_input 冻结输入随之删除，其他检索的输入保留；已删除检索再运行也不会重新冻结输入。

    :aspects: functional, behavior
    """
    jobs = surface_jobs
    job = jobs.create()
    kept = jobs.create('bob')
    adapter.run_search_job(job['search_id'], jobs.surface, 1)
    adapter.run_search_job(kept['search_id'], jobs.surface, 1)
    assert semantic_search_store.stored_run_input(job['search_id']) is not None
    _delete(jobs, job['search_id'])
    assert semantic_search_store.stored_run_input(job['search_id']) is None
    assert semantic_search_store.stored_run_input(kept['search_id']) is not None
    # A run of the deleted search cannot freeze its input again.
    try:
        adapter.run_search_job(job['search_id'], jobs.surface, 1)
    except (SearchRunSuperseded, KeyError, ValueError):
        pass
    assert semantic_search_store.stored_run_input(job['search_id']) is None


def test_failed_cancellation_still_removes_frozen_input(surface_jobs):
    """取消失败时仍删除冻结输入

    删除检索时取消执行失败，检索仍标记为已删除，冻结输入仍被删除。

    :aspects: functional, behavior
    """
    jobs = surface_jobs
    job = jobs.create()
    adapter.run_search_job(job['search_id'], jobs.surface, 1)
    jobs.cancel.side_effect = RuntimeError('SocietyService unavailable')
    with pytest.raises(RuntimeError):
        _delete(jobs, job['search_id'])
    assert jobs.getter(job['search_id'])['deleted'] is True
    assert semantic_search_store.stored_run_input(job['search_id']) is None


def test_searches_trimmed_from_their_list_lose_their_frozen_input():
    """超出列表上限的检索删除冻结输入

    检索列表超过上限被截断时（知识、看板与论文检索），被移出列表的检索的冻结输入一并删除，留在列表中的保留。

    :aspects: functional, behavior
    """
    def freeze(search_id):
        semantic_search_store.run_input(search_id, {'search_id': search_id}, lambda: {'identity': {'search_id': search_id}})
    ids = [f'search_{index:012x}' for index in range(5)]
    for search_id in ids:
        freeze(search_id)
    searches = semantic_search_store.SearchStore('trim_probe_semantic_search', limit=3)
    with searches.locked():
        searches.write([{'search_id': search_id} for search_id in ids])
    assert [job['search_id'] for job in searches.load()] == ids[2:]
    assert [semantic_search_store.stored_run_input(search_id) is not None for search_id in ids] == [False, False, True, True, True]

def test_paper_search_freezes_owner_project_scope_before_batching(tmp_path, monkeypatch, subtests):
    """Paper 语义搜索在分批前冻结工程范围

    授权候选复用 Paper 工程筛选规则，旧全库记录保持兼容，观察不改写范围或重新派发。

    :module: workflow.paper
    :importance: P1
    :aspects: functional, behavior
    """
    jobs = make_surface_jobs('paper', tmp_path, monkeypatch)
    flags = {'has_pdf': False, 'has_analysis': False, 'has_summary': False}
    jobs.candidates['alice'] = [
        {**flags, 'paper_id': jobs.identifier, 'title': 'Linked paper', 'project_ids': ['ACLink']},
        {**flags, 'paper_id': 'paper_1123456789ab', 'title': 'Other paper', 'project_ids': ['RobotWorker']},
        {**flags, 'paper_id': 'paper_2123456789ab', 'title': 'Relation paper', 'relations': {'projects': [{'project_id': 'ACLink'}]}},
        {**flags, 'paper_id': 'paper_3123456789ab', 'title': 'Unassigned paper'},
    ]
    scenarios = [
        ('', [jobs.identifier, 'paper_1123456789ab', 'paper_2123456789ab', 'paper_3123456789ab']),
        ('ACLink', [jobs.identifier, 'paper_2123456789ab']),
        ('project:aclink', [jobs.identifier, 'paper_2123456789ab']),
        ('Unknown', []),
    ]
    for project, expected in scenarios:
        with subtests.test(project=project):
            public = semantic_search_jobs.start_search('paper', {'query': 'neural networks', 'project': project}, actor='alice')
            current = jobs.getter(public['search_id'])
            assert public['project'] == current['project'] == project
            assert [row['paper_id'] for row in adapter.authorized_catalog(current)] == expected
            adapter.run_search_job(public['search_id'], 'paper', 1)
            assert jobs.getter(public['search_id'])['status'] == 'done'
            prompt = jobs.prepare.call_args.kwargs['prompts'][0]
            payload = json.loads(prompt.split('\n', 1)[1])
            assert [row['paper_id'] for row in payload['candidates']] == expected
            assert all(actor == 'alice' for actor in jobs.reads)
            submitted = list(jobs.submitted)
            semantic_search_jobs.get_search('paper', public['search_id'], actor='alice')
            assert jobs.submitted == submitted
            assert jobs.getter(public['search_id'])['project'] == project

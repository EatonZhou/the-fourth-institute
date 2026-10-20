from __future__ import annotations

import copy
import threading
from unittest.mock import Mock

import pytest

from management_portal import business_tasks, semantic_search_jobs, shared_search_adapter as adapter, shared_search_invocation as client
from tests.cases.test_shared_search_adapter import SURFACES, surface_jobs
from tests.cases.test_paper_search_batches import ACTOR, paper_jobs
from tests.cases.test_shared_search_invocation import runtime


# The fixture stubs the starter; recovery tests restore the real one.
REAL_STARTER = semantic_search_jobs.start_task


def bound(jobs, arguments):
    job = arguments['job']
    parent = jobs.plans[tuple(job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation'))]
    batch = next(item for item in parent['batches'] if item['batch_id'] == arguments['batch_id'])
    return parent, batch


def pending(jobs, arguments, *, unknown=False):
    parent, batch = bound(jobs, arguments)
    if batch['message_id'] not in jobs.submitted:
        jobs.submitted.append(batch['message_id'])
    batch['status'] = 'dispatching' if unknown else 'running'
    parent['status'] = 'running'
    arguments['on_assigned'](copy.deepcopy(batch))
    raise client.SharedSearchObservationPending(
        batch, input_state={} if unknown else {'message_id': batch['message_id'], 'status': 'running'},
    )


def worker(jobs, job):
    adapter.run_search_job(job['search_id'], jobs.surface, job['run_generation'])


@pytest.mark.parametrize('unknown', [False, True])
def test_pending_continues_in_same_business_call_on_every_surface(surface_jobs, monkeypatch, unknown):
    """各检索面遇到观察待定时在同一业务调用内继续观察原提交"""
    jobs = surface_jobs
    job = jobs.create()
    observations = []

    def invoke(**arguments):
        observations.append(arguments)
        if len(observations) <= 2:
            pending(jobs, arguments, unknown=unknown)
        bound(jobs, arguments)[1]['status'] = 'completed'  # original Execution finished, not a new dispatch
        return jobs.completion(**arguments)

    def waiting():
        current = jobs.getter(job['search_id'])
        assert current['status'] == 'running' and current['observation_pending']
        assert current['error'] == '' and current['results'] == []
        assert current['run_generation'] == job['run_generation']
        assert current['message_id'] == jobs.submitted[0]
        assert client.read_shared_search_plan(job=current, surface=jobs.surface)['batches'][0]['message_id'] == jobs.submitted[0]
        jobs.cancel.assert_not_called()

    jobs.invoke.side_effect = invoke
    wait = Mock(side_effect=waiting)
    monkeypatch.setattr(adapter, '_wait_for_next_observation', wait)
    worker(jobs, job)

    assert wait.call_count == 2
    assert len(jobs.submitted) == 1
    for field in ('job', 'surface', 'prompt', 'batch_id', 'plan_fingerprint'):
        assert all(call[field] == observations[0][field] for call in observations)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done' and not current['observation_pending']
    assert current['results'] and client.read_shared_search_plan(job=current, surface=jobs.surface)['status'] == 'completed'
    assert 'service_invocation' not in current
    jobs.prepare.assert_called_once()
    jobs.cancel.assert_not_called()


def test_pending_second_batch_keeps_prior_success_and_never_publishes_partial_results(paper_jobs, monkeypatch):
    """第二批次观察待定时保留第一批次结果，且不发布部分结果"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    attempts = []

    def invoke(**arguments):
        attempts.append(arguments['batch_id'])
        if arguments['batch_id'] == 'batch-1' and attempts.count('batch-1') == 1:
            pending(jobs, arguments)
        if arguments['batch_id'] == 'batch-1':
            bound(jobs, arguments)[1]['status'] = 'completed'
        return jobs.complete(**arguments)

    def waiting():
        current = jobs.getter(job['search_id'])
        assert current['completed_batch_count'] == 1
        assert current['results'] == [] and current['status'] == 'running'
        assert client.read_shared_search_plan(job=current, surface=jobs.surface)['batches'][0]['status'] == 'completed'
        jobs.cancel.assert_not_called()

    jobs.invoke.side_effect = invoke
    monkeypatch.setattr(adapter, '_wait_for_next_observation', waiting)
    worker(jobs, job)
    assert attempts == ['batch-0', 'batch-1', 'batch-1', 'batch-2']
    assert len(jobs.submitted) == len(set(jobs.submitted)) == 3
    assert len(jobs.getter(job['search_id'])['results']) == 6


@pytest.mark.parametrize('terminal', ['failed', 'cancelled', 'unreadable'])
def test_authoritative_terminal_after_pending_is_not_retried(paper_jobs, monkeypatch, terminal):
    """观察待定后批次进入失败、取消或不可读终态时不再重试"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    count = 0

    def invoke(**arguments):
        nonlocal count
        count += 1
        if count == 1:
            pending(jobs, arguments)
        parent, batch = bound(jobs, arguments)
        batch['status'] = 'completed' if terminal == 'unreadable' else terminal
        batch['fenced'] = terminal == 'cancelled'
        parent.update(status=batch['status'], fenced=batch['fenced'])
        input_state = {} if terminal == 'unreadable' else {
            'state': terminal, 'message_id': batch['message_id'], 'error': {'message': 'provider read timed out'},
        }
        # Same message for all outcomes: adapter must use structure, not words.
        raise client.SharedSearchInvocationFailed('opaque failure', receipt=batch, input_state=input_state)

    jobs.invoke.side_effect = invoke
    monkeypatch.setattr(adapter, '_wait_for_next_observation', lambda: None)
    with pytest.raises(client.SharedSearchInvocationFailed):
        worker(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == ('cancelled' if terminal == 'cancelled' else 'error')
    assert not current['observation_pending'] and current['results'] == []
    assert current['input_failure']['invocation_status'] == ('completed' if terminal == 'unreadable' else terminal)
    assert count == 2 and len(jobs.submitted) == 1
    jobs.cancel.assert_not_called()
    assert jobs.fail.call_count == (0 if terminal == 'cancelled' else 1)


def test_arbitrary_timeout_is_not_an_observation_pending_signal(paper_jobs, monkeypatch):
    """普通超时错误不视为观察待定，检索不等待直接记为失败"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    jobs.invoke.side_effect = TimeoutError('provider read timed out')
    wait = Mock()
    monkeypatch.setattr(adapter, '_wait_for_next_observation', wait)
    with pytest.raises(TimeoutError):
        worker(jobs, job)
    wait.assert_not_called()
    jobs.invoke.assert_called_once()
    jobs.cancel.assert_not_called()
    jobs.fail.assert_called_once()
    assert jobs.getter(job['search_id'])['status'] == 'error'


@pytest.mark.parametrize('field', ['search_id', 'message_id', 'batch_id', 'plan_fingerprint', 'input_fingerprint'])
def test_pending_from_another_binding_is_rejected(paper_jobs, monkeypatch, field):
    """绑定字段不匹配的观察待定回执被拒绝，检索记为失败"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def wrong(**arguments):
        receipt = copy.deepcopy(bound(jobs, arguments)[1])
        receipt[field] = 'wrong-binding'
        raise client.SharedSearchObservationPending(receipt)

    jobs.invoke.side_effect = wrong
    wait = Mock()
    monkeypatch.setattr(adapter, '_wait_for_next_observation', wait)
    with pytest.raises(ValueError):
        worker(jobs, job)
    wait.assert_not_called()
    jobs.fail.assert_called_once()
    assert jobs.fail.call_args.kwargs['job']['search_id'] == job['search_id']
    jobs.cancel.assert_not_called()
    assert jobs.getter(job['search_id'])['results'] == []


@pytest.mark.parametrize('change', ['refresh', 'delete', 'cancel'])
def test_lifecycle_change_between_observations_never_revives_old_run(paper_jobs, monkeypatch, change):
    """两次观察之间检索被刷新、删除或取消后，旧运行不再恢复"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    jobs.invoke.side_effect = lambda **arguments: pending(jobs, arguments)

    def change_lifecycle():
        patch = {'run_generation': 2, 'status': 'queued'} if change == 'refresh' else {
            'status': 'deleted' if change == 'delete' else 'cancelled', 'deleted': change == 'delete',
        }
        jobs.setter(job['search_id'], patch)

    monkeypatch.setattr(adapter, '_wait_for_next_observation', change_lifecycle)
    worker(jobs, job)
    jobs.invoke.assert_called_once()
    current = jobs.getter(job['search_id'])
    assert current['status'] == {'refresh': 'queued', 'delete': 'deleted', 'cancel': 'cancelled'}[change]
    assert current['results'] == []


def poll(jobs, actor):
    return semantic_search_jobs.list_searches(jobs.surface, actor=actor, project='fixture')


def test_authenticated_polling_recovers_only_marked_inflight_runs(surface_jobs, monkeypatch):
    """已认证轮询只恢复带观察待定标记的运行中检索"""
    jobs = surface_jobs
    job = jobs.create()
    monkeypatch.setattr(semantic_search_jobs, 'start_task', REAL_STARTER)
    start = Mock(return_value=True)
    monkeypatch.setattr(business_tasks, 'start', start)
    jobs.setter(job['search_id'], {'status': 'running', 'observation_pending': True})
    poll(jobs, '')  # internal unauthenticated projection reads must remain read-only
    start.assert_not_called()
    for actor in ('bob', 'alice'):
        poll(jobs, actor)
        assert start.call_args.args[2:] == (jobs.surface, job['search_id'], 1)
        start.reset_mock()
    for patch in ({'status': 'error'}, {'status': 'done'}, {'status': 'cancelled'},
                  {'status': 'running', 'observation_pending': False},
                  {'status': 'running', 'observation_pending': True, 'execution_contract': ''}):
        jobs.setter(job['search_id'], patch)
        poll(jobs, 'alice')
    start.assert_not_called()


def test_stale_poll_cannot_restart_a_refreshed_generation(paper_jobs):
    """过时的轮询不能重启已刷新到新一代的检索"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    old = jobs.setter(job['search_id'], {'status': 'running', 'observation_pending': True})
    jobs.setter(job['search_id'], {'status': 'queued', 'run_generation': 2})
    starter = Mock()
    current = adapter.resume_pending_observation(old, starter)
    assert current['run_generation'] == 2
    starter.assert_not_called()


def test_authenticated_polling_recovers_frozen_plan_without_pending_marker(surface_jobs, monkeypatch):
    """没有待定标记的冻结计划只由已认证用户的轮询恢复"""
    jobs = surface_jobs
    job = jobs.create()
    starter = Mock(side_effect=lambda current: current)
    monkeypatch.setattr(semantic_search_jobs, 'start_task', starter)
    receipt = {field: job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation')}
    receipt.update({'plan_fingerprint': 'frozen-plan', 'is_current_generation': True,
                    'fenced': False, 'status': 'running'})
    jobs.plans[tuple(job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation'))] = receipt
    jobs.setter(job['search_id'], {'status': 'running', 'observation_pending': False})
    poll(jobs, '')
    starter.assert_not_called()
    for actor in ('bob', 'alice'):
        poll(jobs, actor)
        assert starter.call_count == 1
        assert 'service_invocation' not in starter.call_args.args[0]
        starter.reset_mock()


@pytest.mark.parametrize('changed', [
    {'created_by': 'bob'}, {'surface': 'wall'}, {'search_id': 'search_ffffffffffff'},
    {'run_generation': 2}, {'is_current_generation': False}, {'fenced': True},
    {'status': 'cancelled'}, {'status': 'failed'}, {'plan_fingerprint': ''},
])
def test_poll_does_not_recover_unbound_or_fenced_plan(paper_jobs, changed):
    """轮询不恢复绑定不符、fenced 或已终止的检索计划"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    receipt = {field: job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation')}
    receipt.update({'plan_fingerprint': 'frozen-plan', 'is_current_generation': True,
                    'fenced': False, 'status': 'running', **changed})
    jobs.plans[tuple(job[field] for field in ('created_by', 'surface', 'search_id', 'run_generation'))] = receipt
    current = jobs.setter(job['search_id'], {'status': 'running', 'observation_pending': False})
    starter = Mock()
    adapter.resume_pending_observation(current, starter)
    starter.assert_not_called()


@pytest.mark.parametrize('marked_pending', [True, False])
def test_paper_poll_recovers_lost_observer_and_deduplicates_active_business_task(paper_jobs, monkeypatch, marked_pending):
    """Paper 轮询恢复丢失的观察者，沿用原计划且只启动一个业务任务"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def interrupted(**arguments):
        if arguments['batch_id'] == 'batch-0':
            return jobs.complete(**arguments)
        if marked_pending:
            return pending(jobs, arguments)
        jobs.complete(**arguments)
        raise SystemExit('observer lost before recording provider completion')

    jobs.invoke.side_effect = interrupted
    monkeypatch.setattr(adapter, '_wait_for_next_observation', Mock(side_effect=SystemExit('observer lost')))
    with pytest.raises(SystemExit):
        worker(jobs, job)
    old = jobs.getter(job['search_id'])
    old_plan = client.read_shared_search_plan(job=old, surface=jobs.surface)
    assert bool(old.get('observation_pending')) is marked_pending
    assert old['status'] == 'running'
    assert old['completed_batch_count'] == 1
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def resumed(**arguments):
        started.set()
        assert release.wait(2)
        _, batch = bound(jobs, arguments)
        if batch['message_id'] in jobs.submitted:
            batch['status'] = 'completed'
        return jobs.complete(**arguments)

    jobs.invoke.side_effect = resumed
    monkeypatch.setattr(semantic_search_jobs, 'start_task', REAL_STARTER)
    real_worker = semantic_search_jobs.run_job

    def observe(*args):
        try:
            real_worker(*args)
        finally:
            finished.set()

    tracked = Mock(side_effect=observe)
    monkeypatch.setattr(semantic_search_jobs, 'run_job', tracked)
    try:
        semantic_search_jobs.get_search('paper', job['search_id'], actor=ACTOR)
        assert started.wait(2)
        semantic_search_jobs.get_search('paper', job['search_id'], actor=ACTOR)
        poll(jobs, ACTOR)
        assert tracked.call_count == 1
    finally:
        release.set()
        assert finished.wait(3)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done'
    assert len(current['results']) == 6
    assert current['run_generation'] == 1
    current_plan = client.read_shared_search_plan(job=current, surface=jobs.surface)
    assert current_plan['plan_fingerprint'] == old_plan['plan_fingerprint']
    assert [batch['message_id'] for batch in current_plan['batches']] == [
        batch['message_id'] for batch in old_plan['batches']]
    assert len(jobs.submitted) == len(set(jobs.submitted)) == 3
    jobs.cancel.assert_not_called()


@pytest.mark.parametrize(
    'surface_jobs', [surface for surface in SURFACES if surface not in {'paper', 'wall', 'todo', 'review'}], indirect=True)
def test_knowledge_detail_poll_recovers_for_admitted_actors_without_owner_gate(surface_jobs, monkeypatch):
    """知识详情轮询为任何已准入用户恢复检索，不限于创建者"""
    jobs = surface_jobs
    job = jobs.create()
    jobs.setter(job['search_id'], {'status': 'running', 'observation_pending': True})
    starter = Mock(side_effect=lambda job: job)
    monkeypatch.setattr(semantic_search_jobs, 'start_task', starter)
    view = lambda actor: semantic_search_jobs.search_view(
        job['search_id'], actor=actor, surfaces=semantic_search_jobs.RESOURCE_SCOPES)
    with pytest.raises(KeyError):
        view('')
    starter.assert_not_called()
    for actor in ('bob', 'alice'):
        view(actor)
        starter.assert_called_once()
        assert starter.call_args.args[0]['created_by'] == job['created_by']
        starter.reset_mock()


from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import business_tasks, semantic_search_jobs, shared_search_adapter
from management_portal.semantic_search_runs import SearchRunSuperseded


@pytest.fixture(params=['knowledge', 'paper'])
def jobs(request, monkeypatch):
    surface = request.param
    monkeypatch.setattr(semantic_search_jobs, 'publish_local_projection_change', Mock())
    starter = semantic_search_jobs.start_task
    launch = Mock(side_effect=lambda job: job)
    monkeypatch.setattr(semantic_search_jobs, 'start_task', launch)
    delegate = Mock()
    monkeypatch.setattr(shared_search_adapter, 'run_search_job', delegate)
    return SimpleNamespace(
        surface=surface,
        start=lambda payload: semantic_search_jobs.start_search(surface, payload, actor='alice'),
        refresh=lambda search_id: semantic_search_jobs.refresh_search(surface, {'search_id': search_id}, actor='alice'),
        delete=lambda search_id: semantic_search_jobs.delete_search(surface, {'search_id': search_id}, actor='alice'),
        setter=lambda search_id, patch, **kwargs: semantic_search_jobs.set_search(surface, search_id, patch, **kwargs),
        getter=lambda search_id: semantic_search_jobs.search_by_id(surface, search_id),
        starter=starter,
        worker=lambda search_id, generation: semantic_search_jobs.run_job(surface, search_id, generation),
        delegate=delegate, launch=launch,
    )


def new_job(jobs):
    public = jobs.start({'query': 'fixture query', 'run_generation': 99,
                         'created_by': 'bob', 'surface': 'wall', 'execution_profile': {'provider': 'forged'}})
    return jobs.getter(public['search_id'])


def test_new_job_owns_generation_creator_and_surface(jobs):
    """新建检索的代次、创建者和页面由服务端设定，忽略请求中伪造的字段"""
    job = new_job(jobs)
    assert job['run_generation'] == 1
    assert job['created_by'] == 'alice'
    assert job['surface'] == jobs.surface
    assert 'execution_profile' not in job


def test_refresh_claims_exactly_one_new_run_under_concurrency(jobs):
    """并发刷新同一检索只开启一次新运行，代次只加一"""
    job = new_job(jobs)
    jobs.setter(job['search_id'], {'status': 'done', 'notes': 'old', 'results': [{'reason': 'old'}]})
    jobs.launch.reset_mock()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda unused: jobs.refresh(job['search_id']), range(24)))
    current = jobs.getter(job['search_id'])
    assert current['run_generation'] == 2
    assert current['notes'] == ''
    assert current['results'] == ([{'reason': 'old'}] if jobs.surface == 'paper' else [])
    jobs.launch.assert_called_once()


@pytest.mark.parametrize('terminal', ['deleted', 'cancelled', 'done', 'error'])
def test_terminal_state_fences_worker_writes(jobs, terminal):
    """检索已删除、取消、完成或失败后，运行中的 worker 不能再写入结果"""
    job = new_job(jobs)
    jobs.setter(job['search_id'], {'status': terminal, 'deleted': terminal == 'deleted'})
    with pytest.raises(SearchRunSuperseded):
        jobs.setter(job['search_id'], {'status': 'done', 'results': ['late']},
                    expected_generation=1, expected_status=('queued', 'running'))
    assert jobs.getter(job['search_id'])['status'] == terminal
    assert jobs.getter(job['search_id'])['results'] == []


def test_old_generation_and_missing_job_cannot_be_recreated(jobs):
    """旧代次的写入被拒绝，不存在的检索也不会因写入被重新创建"""
    job = new_job(jobs)
    jobs.setter(job['search_id'], {'status': 'done'})
    jobs.refresh(job['search_id'])
    for search_id in (job['search_id'], 'search_000000000000'):
        with pytest.raises(SearchRunSuperseded):
            jobs.setter(search_id, {'status': 'done'}, expected_generation=1, expected_status=('queued', 'running'))
    assert jobs.getter('search_000000000000') is None


def test_delayed_task_does_not_adopt_new_generation(jobs):
    """延迟启动的旧代任务不接管刷新后的新代运行"""
    job = new_job(jobs)
    jobs.setter(job['search_id'], {'status': 'done'})
    jobs.refresh(job['search_id'])
    jobs.worker(job['search_id'], 1)
    jobs.delegate.assert_not_called()
    assert jobs.getter(job['search_id'])['status'] == 'queued'


def test_launch_callback_is_bound_to_original_run(jobs, monkeypatch):
    """启动失败回调绑定原代次，刷新后迟到的失败不改写新运行状态"""
    job = new_job(jobs)
    launch = Mock()
    monkeypatch.setattr(business_tasks, 'start', launch)
    jobs.starter(job)
    assert launch.call_args.args[2:] == (jobs.surface, job['search_id'], 1)
    assert launch.call_args.args[0].endswith(':1')
    callback = launch.call_args.kwargs['on_error']
    jobs.setter(job['search_id'], {'status': 'done'})
    jobs.refresh(job['search_id'])
    callback(RuntimeError('late launch failure'))
    assert jobs.getter(job['search_id'])['status'] == 'queued'
    assert jobs.getter(job['search_id'])['error'] == ''


def test_catalog_surface_is_persisted_in_authoritative_job(monkeypatch):
    """检索记录保存发起检索的目录页面，不采用请求中的 resource_scope"""
    monkeypatch.setattr(semantic_search_jobs, 'start_task', lambda job: job)
    result = semantic_search_jobs.start_search('repo', {'query': 'fixture', 'resource_scope': 'knowledge'}, actor='alice')
    assert semantic_search_jobs.search_by_id('repo', result['search_id'])['surface'] == 'repo'

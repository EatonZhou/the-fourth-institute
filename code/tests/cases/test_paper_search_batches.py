from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from management_portal import semantic_search_jobs, semantic_search_store, shared_search_adapter as adapter, shared_search_invocation
from management_portal.resources.registry_store import RegistryWriter
from tests.cases.registry_support import put_registry_document
from tests.cases.test_shared_search_adapter import make_surface_jobs


ACTOR = 'test-actor'
COMPLETE_INPUT_PLANNER = adapter.candidate_prompts


def catalog(count, summary_size=500):
    return [{'paper_id': f'paper_{index:012x}', 'title': f'Paper {index}: multi-agent collaboration',
             'abstract': '多智能体协作与任务规划。' * (summary_size // 12),
             'tags': ['planning', 'collaboration']} for index in range(count)]


def prompt_rows(prompt):
    return json.loads(prompt.split('\n', 1)[1])['candidates']


@pytest.fixture
def paper_jobs(tmp_path, monkeypatch):
    jobs = make_surface_jobs('paper', tmp_path, monkeypatch)
    jobs.candidates[ACTOR] = catalog(6)
    # Simulate inputs frozen by the old planner to exercise batch recovery.
    # New searches use the complete-input planner tested separately below.
    monkeypatch.setattr(adapter, 'candidate_prompts', lambda job, rows: [
        adapter.candidate_prompt(job, rows[index:index + 2])
        for index in range(0, len(rows), 2)
    ] or [adapter.candidate_prompt(job, [])])

    def complete(**kwargs):
        result = jobs.completion(**kwargs)
        rows = prompt_rows(kwargs['prompt'])
        result['message'] = json.dumps({'results': [
            {'paper_id': row['paper_id'], 'score': 70 + int(row['paper_id'][-2:], 16) % 30,
             'reason': row['title']} for row in rows
        ], 'query_interpretation': 'multi-agent collaboration', 'notes': 'batch evaluated'})
        return result

    jobs.complete = complete
    jobs.invoke.side_effect = complete
    return jobs


def run(jobs, job):
    adapter.run_search_job(job['search_id'], 'paper', job['run_generation'])


def test_3417_candidates_use_one_input_without_dropping_or_shortening_metadata():
    """3417 个论文候选合为完整输入且不丢弃或缩短元数据"""
    records = catalog(3417, summary_size=1700)
    job = {'surface': 'paper', 'query': '多智能体协作与任务规划'}
    prompts = adapter.candidate_prompts(job, records)

    assert len(prompts) == 1
    assert len(prompts[0]) > 96_000
    assert [row for prompt in prompts for row in prompt_rows(prompt)] == records
    assert adapter.candidate_prompts(job, records) == prompts


def test_complete_input_preserves_unicode_and_json_escaping():
    """完整检索输入保留 Unicode 与 JSON 转义字符"""
    records = catalog(2)
    records[0]['title'] = '"quoted" \\ title\n换行 🧠'
    job = {'surface': 'paper', 'query': 'query\n"带引号"'}
    prompts = adapter.candidate_prompts(job, records)
    assert len(prompts) == 1
    assert json.loads(prompts[0].split('\n', 1)[1]) == {**job, 'candidates': records}


def test_paper_worker_handles_3417_candidates_and_returns_last_candidate_match(paper_jobs, monkeypatch):
    """论文检索处理 3417 个候选并返回末尾候选匹配"""
    jobs = paper_jobs
    monkeypatch.setattr(adapter, 'candidate_prompts', COMPLETE_INPUT_PLANNER)
    # Approximately the English per-record size in the live diagnostics; the
    # separate planner test covers long multilingual records without shortening.
    jobs.candidates[ACTOR] = catalog(3417)
    for row in jobs.candidates[ACTOR]:
        row['abstract'] = 'multi-agent collaboration and task planning. ' * 20
    job = jobs.create(ACTOR)
    job = jobs.setter(job['search_id'], {'query': '多智能体协作与任务规划'})
    last_id = jobs.candidates[ACTOR][-1]['paper_id']

    def last_match(**kwargs):
        result = jobs.complete(**kwargs)
        ids = {row['paper_id'] for row in prompt_rows(kwargs['prompt'])}
        result['message'] = json.dumps({'results': [
            {'paper_id': last_id, 'score': 100, 'reason': 'last candidate is relevant'},
        ] if last_id in ids else []})
        return result

    jobs.invoke.side_effect = last_match
    semantic_search_jobs.run_job('paper', job['search_id'], 1)

    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done'
    assert [row['paper_id'] for row in current['results']] == [last_id]
    assert current['batch_count'] == current['completed_batch_count'] == 1
    prompts = jobs.prepare.call_args.kwargs['prompts']
    assert len(prompts) == 1 and len(prompts[0]) > 96_000
    assert [row for prompt in prompts for row in prompt_rows(prompt)] == jobs.candidates[ACTOR]
    assert len(jobs.submitted) == len(prompts)
    assert set(jobs.reads) == {ACTOR}
    assert all(call.kwargs['job']['search_id'] == job['search_id'] and
               call.kwargs['job']['run_generation'] == 1 for call in jobs.invoke.call_args_list)
    snapshot = semantic_search_store.stored_run_input(job['search_id'])
    assert snapshot['catalog'] == jobs.candidates[ACTOR]
    assert snapshot['prompts'] == prompts


def test_batch_results_cannot_inject_ids_from_another_authorized_batch(paper_jobs):
    """论文批次结果拒绝注入其他已授权批次的候选标识"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    last_id = jobs.candidates[ACTOR][-1]['paper_id']

    def inject(**kwargs):
        result = jobs.complete(**kwargs)
        parsed = json.loads(result['message'])
        parsed['results'].insert(0, {'paper_id': last_id, 'score': 100, 'reason': 'cross-batch guess'})
        if last_id in {row['paper_id'] for row in prompt_rows(kwargs['prompt'])}:
            parsed['results'] = [row for row in parsed['results'] if row['reason'] != 'cross-batch guess']
        result['message'] = json.dumps(parsed)
        return result

    jobs.invoke.side_effect = inject
    run(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done'
    assert len(current['results']) == 6
    assert all(row['reason'] != 'cross-batch guess' for row in current['results'])
    assert [row['score'] for row in current['results']] == sorted(
        [row['score'] for row in current['results']], reverse=True,
    )


def test_crash_recovery_reuses_frozen_complete_plan_and_completed_executions(paper_jobs):
    """检索崩溃恢复复用冻结计划与已完成执行"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def crash_before_second(**kwargs):
        if len(jobs.submitted) == 1:
            raise SystemExit('simulated worker exit')
        return jobs.complete(**kwargs)

    jobs.invoke.side_effect = crash_before_second
    with pytest.raises(SystemExit):
        run(jobs, job)
    first_prompts = jobs.prepare.call_args.kwargs['prompts']
    first_execution = jobs.submitted[0]
    jobs.candidates[ACTOR].append({'paper_id': 'paper_ffffffffffff', 'title': 'new candidate'})
    jobs.invoke.side_effect = jobs.complete

    run(jobs, job)

    assert jobs.getter(job['search_id'])['status'] == 'done'
    assert jobs.prepare.call_args.kwargs['prompts'] == first_prompts
    assert jobs.submitted.count(first_execution) == 1
    assert len(jobs.submitted) == len(first_prompts)
    assert 'paper_ffffffffffff' not in ''.join(first_prompts)


def test_revoke_before_first_batch_prevents_any_dispatch(paper_jobs):
    """首批前撤销论文权限阻止全部检索投递"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    prepare = jobs.prepare.side_effect

    def revoke(**kwargs):
        receipt = prepare(**kwargs)
        jobs.candidates[ACTOR] = []
        return receipt

    jobs.prepare.side_effect = revoke
    with pytest.raises(PermissionError, match='before batch dispatch'):
        run(jobs, job)
    assert jobs.submitted == []
    assert jobs.getter(job['search_id'])['status'] == 'error'
    jobs.fail.assert_called_once()
    assert jobs.fail.call_args.kwargs['job']['created_by'] == ACTOR
    jobs.cancel.assert_not_called()


def test_revoke_between_batches_stops_without_partial_done(paper_jobs):
    """批次之间撤销论文权限会停止检索且不发布部分结果"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def revoke_next(**kwargs):
        result = jobs.complete(**kwargs)
        completed_ids = {row['paper_id'] for row in prompt_rows(kwargs['prompt'])}
        jobs.candidates[ACTOR] = [row for row in jobs.candidates[ACTOR] if row['paper_id'] in completed_ids]
        return result

    jobs.invoke.side_effect = revoke_next
    with pytest.raises(PermissionError, match='before batch dispatch'):
        run(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'error'
    assert current['results'] == []
    assert len(jobs.submitted) == 1
    jobs.fail.assert_called_once()
    jobs.cancel.assert_not_called()


def test_read_permission_is_rechecked_in_assignment_immediately_before_submission(paper_jobs):
    """论文检索在分配后提交前再次检查候选权限"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def revoke_before_assignment(**kwargs):
        jobs.candidates[ACTOR] = []
        return jobs.complete(**kwargs)

    jobs.invoke.side_effect = revoke_before_assignment
    with pytest.raises(PermissionError, match='before batch dispatch'):
        run(jobs, job)
    assert jobs.submitted == []
    assert jobs.getter(job['search_id'])['status'] == 'error'


def test_final_read_check_filters_matches_revoked_after_successful_execution(paper_jobs):
    """论文检索成功后按最新权限过滤已撤销匹配"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    removed = jobs.candidates[ACTOR][0]['paper_id']

    def revoke_completed(**kwargs):
        result = jobs.complete(**kwargs)
        if len(jobs.submitted) == len(jobs.prepare.call_args.kwargs['prompts']):
            jobs.candidates[ACTOR] = [row for row in jobs.candidates[ACTOR] if row['paper_id'] != removed]
        return result

    jobs.invoke.side_effect = revoke_completed
    run(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done'
    assert removed not in {row['paper_id'] for row in current['results']}
    assert len(current['results']) == 5


@pytest.mark.parametrize('failure', [RuntimeError('provider failure'), ValueError('invalid JSON')])
def test_later_batch_failure_fences_parent_without_publishing_partial_results(paper_jobs, failure):
    """后续检索批次失败终止父调用且不发布部分结果"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def fail_second(**kwargs):
        if jobs.submitted:
            raise failure
        return jobs.complete(**kwargs)

    jobs.invoke.side_effect = fail_second
    with pytest.raises(type(failure)):
        run(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'error'
    assert current['results'] == []
    assert len(jobs.submitted) == 1
    jobs.fail.assert_called_once()
    jobs.cancel.assert_not_called()


@pytest.mark.parametrize('patch', [{'fenced': True}, {'is_current_generation': False}, {'status': 'running'}])
def test_final_parent_must_be_current_unfenced_and_completed(paper_jobs, patch):
    """检索发布前要求父调用已完成且处于当前有效代次"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    observe = jobs.observe.side_effect

    def changed_parent(**kwargs):
        observed = observe(**kwargs)
        if observed['receipt']['status'] == 'completed':
            observed['receipt'].update(patch)
        return observed

    jobs.observe.side_effect = changed_parent
    invalidated = patch.get('fenced') or patch.get('is_current_generation') is False
    cancelled = patch.get('is_current_generation') is False
    with pytest.raises(shared_search_invocation.SharedSearchInvocationFailed if invalidated else ValueError):
        run(jobs, job)
    assert jobs.getter(job['search_id'])['status'] == ('cancelled' if cancelled else 'error')
    assert jobs.getter(job['search_id'])['results'] == []


@pytest.mark.parametrize('change', ['delete', 'refresh'])
def test_delete_or_refresh_during_batch_never_writes_old_results(paper_jobs, change):
    """检索批次中删除或刷新任务后不写回旧结果"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def replace_run(**kwargs):
        result = jobs.complete(**kwargs)
        patch = {'deleted': True, 'status': 'deleted'} if change == 'delete' else {'run_generation': 2, 'status': 'queued'}
        jobs.setter(job['search_id'], patch)
        assert kwargs['cancellation_requested']()
        return result

    jobs.invoke.side_effect = replace_run
    run(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == ('deleted' if change == 'delete' else 'queued')
    assert current['results'] == []
    assert jobs.invoke.call_count == 1
    assert jobs.cancel.call_args.kwargs['job']['run_generation'] == 1


def test_empty_catalog_still_uses_one_isolated_batch(paper_jobs):
    """空论文目录仍以一个独立批次执行检索"""
    jobs = paper_jobs
    jobs.candidates[ACTOR] = []
    job = jobs.create(ACTOR)
    run(jobs, job)
    assert jobs.getter(job['search_id'])['status'] == 'done'
    assert jobs.getter(job['search_id'])['results'] == []
    assert len(jobs.prepare.call_args.kwargs['prompts']) == 1
    jobs.invoke.assert_called_once()


def test_large_single_candidate_reaches_execution_without_shortening(paper_jobs, monkeypatch):
    """超长单条论文候选完整进入执行且不缩短"""
    jobs = paper_jobs
    monkeypatch.setattr(adapter, 'candidate_prompts', COMPLETE_INPUT_PLANNER)
    jobs.candidates[ACTOR] = catalog(1, summary_size=100_000)
    job = jobs.create(ACTOR)
    run(jobs, job)
    jobs.invoke.assert_called_once()
    assert prompt_rows(jobs.invoke.call_args.kwargs['prompt']) == jobs.candidates[ACTOR]
    assert jobs.getter(job['search_id'])['status'] == 'done'


def test_corrupted_snapshot_cannot_silently_drop_a_batch(paper_jobs):
    """损坏快照缺少批次时拒绝准备检索"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    payload = adapter._input_snapshot(job, jobs.candidates[ACTOR])
    payload['prompts'].pop()
    semantic_search_store.delete_run_inputs([job['search_id']])
    semantic_search_store.run_input(job['search_id'], payload['identity'], lambda: payload)
    with pytest.raises(ValueError, match='complete candidate catalog'):
        run(jobs, job)
    jobs.prepare.assert_not_called()


def test_duplicate_worker_does_not_cancel_an_already_completed_run(paper_jobs):
    """重复工作者不取消其他工作者已完成的检索"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def other_worker_finished(**kwargs):
        result = jobs.complete(**kwargs)
        jobs.setter(job['search_id'], {'status': 'done', 'results': [{'paper_id': jobs.identifier, 'score': 90}]})
        return result

    jobs.invoke.side_effect = other_worker_finished
    run(jobs, job)
    assert jobs.getter(job['search_id'])['status'] == 'done'
    jobs.cancel.assert_not_called()


def test_late_worker_error_cannot_cancel_another_workers_completed_run(paper_jobs, monkeypatch):
    """迟到工作者解析失败不取消其他工作者已完成结果"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)

    def completed_elsewhere(message):
        jobs.setter(job['search_id'], {'status': 'done', 'results': [{'paper_id': jobs.identifier, 'score': 90}]})
        raise ValueError('late worker parsing error')

    monkeypatch.setattr(adapter, '_extract_result', completed_elsewhere)
    run(jobs, job)
    assert jobs.getter(job['search_id'])['status'] == 'done'
    jobs.cancel.assert_not_called()


def test_failure_finalization_remains_visible_without_partial_results(paper_jobs):
    """检索失败收尾异常仍保留原始错误且不发布部分结果"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    jobs.invoke.side_effect = RuntimeError('provider failure')
    jobs.fail.side_effect = RuntimeError('failure finalization unavailable')
    with pytest.raises(RuntimeError, match='provider failure'):
        run(jobs, job)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'error'
    assert current['results'] == []
    jobs.fail.assert_called_once()
    jobs.cancel.assert_not_called()
    assert 'provider failure' in current['error']
    assert 'parent error finalization failed: failure finalization unavailable' in current['error']


def test_snapshot_atomic_failure_does_not_leave_partial_input(paper_jobs, monkeypatch):
    """检索快照写入失败时不留下部分输入"""
    jobs = paper_jobs
    job = jobs.create(ACTOR)
    from management_common.postgres_database import PostgresConnection
    execute = PostgresConnection.execute
    def fail_snapshot(connection, statement, parameters=None):
        if str(statement).startswith('INSERT INTO run_inputs'):
            raise OSError('fixture write failure')
        return execute(connection, statement, parameters)
    monkeypatch.setattr(PostgresConnection, 'execute', fail_snapshot)
    with pytest.raises(OSError, match='fixture write failure'):
        adapter._input_snapshot(job, jobs.candidates[ACTOR])
    assert semantic_search_store.stored_run_input(job['search_id']) is None

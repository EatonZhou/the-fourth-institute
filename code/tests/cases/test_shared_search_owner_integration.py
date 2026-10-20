from __future__ import annotations

import json

import pytest

from management_portal import resident_handoff, shared_search_adapter, shared_search_invocation
from management_portal.society_service import service_invocation_domain
from tests.cases.test_shared_search_adapter import surface_jobs, make_surface_jobs
from tests.cases.test_paper_search_batches import ACTOR, catalog, prompt_rows
from tests.cases.test_shared_search_invocation import runtime


REAL_RUN = resident_handoff.run_shared_search_turn
REAL_PREPARE = shared_search_invocation.prepare_shared_search_batches
REAL_BATCH = shared_search_invocation.run_shared_search_batch
REAL_GET = shared_search_invocation.get_shared_search_invocation
REAL_READ = shared_search_invocation.read_shared_search_plan
REAL_CANCEL = shared_search_invocation.cancel_shared_search_invocation
REAL_FAIL = shared_search_invocation.fail_shared_search_invocation


def connect(runtime, jobs, monkeypatch):
    runtime.binding['surface'] = jobs.surface
    monkeypatch.setattr(resident_handoff, 'run_shared_search_turn', REAL_RUN)
    monkeypatch.setattr(shared_search_invocation, 'prepare_shared_search_batches', REAL_PREPARE)
    monkeypatch.setattr(shared_search_invocation, 'run_shared_search_batch', REAL_BATCH)
    monkeypatch.setattr(shared_search_invocation, 'get_shared_search_invocation', REAL_GET)
    monkeypatch.setattr(shared_search_invocation, 'read_shared_search_plan', REAL_READ)
    monkeypatch.setattr(shared_search_invocation, 'cancel_shared_search_invocation', REAL_CANCEL)
    monkeypatch.setattr(shared_search_invocation, 'fail_shared_search_invocation', REAL_FAIL)
    original_status = runtime.executions.status
    def status(execution_id, **kwargs):
        result = original_status(execution_id, **kwargs)
        result['result']['output']['message'] = json.dumps({
            'results': [{jobs.key: jobs.identifier, 'score': 91, 'reason': kwargs['actor'] + ' result'}],
        })
        return result
    monkeypatch.setattr(runtime.executions, 'status', status)


def test_page_to_formal_owner_keeps_actor_execution_and_agent_data_isolated(runtime, surface_jobs, monkeypatch):
    """不同用户的检索共用同一 Resident，但执行与历史按用户隔离"""
    jobs = surface_jobs
    connect(runtime, jobs, monkeypatch)
    alice = jobs.create('alice')
    bob = jobs.create('bob')
    shared_search_adapter.run_search_job(alice['search_id'], jobs.surface, 1)
    shared_search_adapter.run_search_job(bob['search_id'], jobs.surface, 1)
    first = jobs.getter(alice['search_id'])
    second = jobs.getter(bob['search_id'])
    assert first['status'] == second['status'] == 'done'
    first_plan = REAL_READ(job=first, surface=jobs.surface)
    second_plan = REAL_READ(job=second, surface=jobs.surface)
    assert 'service_invocation' not in first and 'service_invocation' not in second
    assert first_plan['resident_id'] == second_plan['resident_id']
    assert first_plan['message_id'] != second_plan['message_id']
    assert first['results'][0]['reason'] == 'alice result'
    assert second['results'][0]['reason'] == 'bob result'
    assert [request.actor for request in runtime.executions.submitted] == ['alice', 'bob']
    for request in runtime.executions.submitted:
        assert request.session.mode == "stateless"
        assert request.session.binding_id == ""
        assert request.session.context_source_binding_id == ""
        assert 'other user history' not in request.prompt


def test_recovery_after_binding_withdrawal_observes_original_receipt_without_dispatch(runtime, surface_jobs, monkeypatch):
    """调用授权撤回后恢复检索只观察原回执，不重新派发"""
    jobs = surface_jobs
    connect(runtime, jobs, monkeypatch)
    job = jobs.create()
    shared_search_adapter.run_search_job(job['search_id'], jobs.surface, 1)
    first = REAL_READ(job=jobs.getter(job['search_id']), surface=jobs.surface)
    jobs.setter(job['search_id'], {'status': 'running'})
    runtime.registry.resolve.side_effect = PermissionError('new invocation grant withdrawn')
    shared_search_adapter.run_search_job(job['search_id'], jobs.surface, 1)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done'
    current_plan = REAL_READ(job=current, surface=jobs.surface)
    assert current_plan['message_id'] == first['message_id']
    assert current_plan['binding'] == first['binding']
    assert len(runtime.executions.submitted) == 1


def test_completed_generation_history_remains_observable_after_refresh(runtime):
    """检索刷新后，上一代已完成的调用记录仍可读取"""
    previous = {"created_by": "alice", "surface": "knowledge", "search_id": "search_112233445566", "run_generation": 1}
    current = {**previous, "run_generation": 2}
    first = REAL_RUN(job=previous, surface="knowledge", prompt="first authorized input")
    second = REAL_RUN(job=current, surface="knowledge", prompt="refreshed authorized input")
    assert first["message_id"] != second["message_id"]
    old = runtime.owner.get_invocation(job=previous, surface="knowledge")
    assert old["receipt"]["status"] == "completed"
    assert old["input_state"]["message_id"] == first["message_id"]


@pytest.mark.parametrize('candidate_count', [6, 3417])
def test_complete_paper_catalog_reaches_formal_owner_and_replays_without_redispatch(runtime, tmp_path, monkeypatch, candidate_count):
    """完整论文目录一次提交给正式执行方，重放时不重新派发"""
    jobs = make_surface_jobs('paper', tmp_path, monkeypatch)
    jobs.candidates[ACTOR] = catalog(candidate_count, summary_size=1700)
    service_invocation_domain.get_portal_access_store().is_active.side_effect = lambda actor: actor == ACTOR
    connect(runtime, jobs, monkeypatch)
    last_id = jobs.candidates[ACTOR][-1]['paper_id']
    original_status = runtime.executions.status

    def status(execution_id, **kwargs):
        observed = original_status(execution_id, **kwargs)
        request = runtime.executions.requests[execution_id]
        ids = {row['paper_id'] for row in prompt_rows(request.prompt)}
        observed['result']['output']['message'] = json.dumps({'results': [
            {'paper_id': last_id, 'score': 100, 'reason': 'relevant tail candidate'},
        ] if last_id in ids else []})
        return observed

    monkeypatch.setattr(runtime.executions, 'status', status)
    job = jobs.create(ACTOR)
    shared_search_adapter.run_search_job(job['search_id'], 'paper', 1)
    current = jobs.getter(job['search_id'])
    assert current['status'] == 'done'
    assert [row['paper_id'] for row in current['results']] == [last_id]
    receipt = REAL_READ(job=current, surface=jobs.surface)
    requests = list(runtime.executions.submitted)
    assert len(requests) == len(receipt['batches']) == 1
    assert len({request.execution_id for request in requests}) == len(requests)
    assert len({request.agent_id for request in requests}) == 1
    assert all(request.actor == ACTOR and request.session.mode == 'stateless' and
               request.session.binding_id == '' and request.session.context_source_binding_id == ''
               for request in requests)
    if candidate_count == 3417:
        assert len(requests[0].prompt) > 2_000_000
    assert [row for request in requests for row in prompt_rows(request.prompt)] == jobs.candidates[ACTOR]
    jobs.setter(job['search_id'], {'status': 'running'})
    shared_search_adapter.run_search_job(job['search_id'], 'paper', 1)
    assert len(runtime.executions.submitted) == len(requests)
    assert REAL_READ(job=jobs.getter(job['search_id']), surface=jobs.surface)['plan_fingerprint'] == receipt['plan_fingerprint']

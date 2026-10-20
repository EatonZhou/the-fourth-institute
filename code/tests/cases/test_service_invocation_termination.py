"""
:module: lineage.service
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations

import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tests.cases.rpc_test_support import socket_rpc_authenticator

from management_portal.society_service import server, service_invocation_domain as domain
from management_portal.society_service.contracts import operation_contract
from management_portal.society_service.transport import SocietyServiceClient
from management_portal.service_invocation_contract import ServiceInvocationConflict, batch_manifest
from tests.cases.test_shared_search_invocation import job, runtime


PROMPTS = ["completed input", "failed input", "in-flight input", "unsubmitted input"]


def prepare(runtime, selected=None):
    selected = selected or job()
    return runtime.owner.prepare_batches(job=selected, surface=selected["surface"],
                                         batches=batch_manifest(selected, PROMPTS))


def submit(runtime, plan, index):
    return runtime.owner.submit_batch(job=job(generation=plan["run_generation"]), surface="knowledge",
        batch_id=plan["batches"][index]["batch_id"], plan_fingerprint=plan["plan_fingerprint"], prompt=PROMPTS[index])


def observe(plan, index, status):
    return domain._store().observe(job(generation=plan["run_generation"]), "knowledge",
        plan["batches"][index]["batch_id"], plan["plan_fingerprint"], status)


def fail(runtime, plan, selected=None):
    return runtime.owner.fail_invocation(job=selected or job(generation=plan["run_generation"]),
        surface="knowledge", plan_fingerprint=plan["plan_fingerprint"])


def test_failure_fences_without_overwriting_batch_outcomes(runtime):
    """计划失败只封禁派发并保留各批次真实状态与身份"""
    plan = prepare(runtime)
    for index in range(3):
        submit(runtime, plan, index)
    observe(plan, 0, "completed")
    observe(plan, 1, "failed")
    result = fail(runtime, plan)
    assert result["fenced"] and result["fence_reason"] == "failed"
    receipt = result["receipt"]
    assert receipt["status"] == "failed" and not receipt["cancel_requested"]
    assert [batch["status"] for batch in receipt["batches"]] == ["completed", "failed", "queued", "prepared"]
    assert all(batch["fenced"] and batch["fence_reason"] == "failed" for batch in receipt["batches"])
    assert runtime.executions.cancelled == [plan["batches"][2]["message_id"]]
    assert receipt["batches"][2]["cancel_delivered"]
    assert submit(runtime, plan, 3)["status"] == "prepared"
    assert len(runtime.executions.submitted) == 3
    assert fail(runtime, plan) == result
    assert runtime.executions.cancelled == [plan["batches"][2]["message_id"]]
    for before, after in zip(plan["batches"], receipt["batches"]):
        for field in ("message_id", "resident_id", "input_fingerprint", "plan_fingerprint", "binding"):
            assert before[field] == after[field]


def test_business_failure_can_follow_completed_executions_without_cancelling_them(runtime):
    """业务失败可终结已完成执行的父计划且不取消成功批次"""
    plan = prepare(runtime)
    for index in range(len(PROMPTS)):
        submit(runtime, plan, index)
        observe(plan, index, "completed")
    result = fail(runtime, plan)
    assert result["receipt"]["status"] == "failed"
    assert all(batch["status"] == "completed" for batch in result["receipt"]["batches"])
    assert not runtime.executions.cancelled


@pytest.mark.parametrize("control,reason", [("cancel", "cancelled"), ("delete", "deleted"), ("refresh", "superseded")])
def test_explicit_controls_keep_terminal_and_unsubmitted_batch_statuses(runtime, control, reason):
    """显式取消删除刷新保留终态与未派发批次状态"""
    plan = prepare(runtime)
    for index, status in enumerate(("completed", "failed", "cancelled")):
        observe(plan, index, status)
    if control == "refresh":
        prepare(runtime, job(generation=2))
    else:
        runtime.owner.cancel_invocation(job=job(), surface="knowledge", deleted=control == "delete")
    result = runtime.owner.read_plan(job=job(), surface="knowledge")
    assert result["fence_reason"] == reason
    assert [batch["status"] for batch in result["batches"]] == ["completed", "failed", "cancelled", "prepared"]
    assert result["is_current_generation"] == (control == "cancel")
    assert not runtime.executions.cancelled
    assert fail(runtime, plan)["fence_reason"] == reason
    with pytest.raises(ServiceInvocationConflict, match="fenced"):
        domain._store().claim_dispatch(job(), "knowledge", plan["batches"][3]["batch_id"], plan["plan_fingerprint"])


@pytest.mark.parametrize("control", ["cancel", "delete", "refresh"])
def test_failure_reason_survives_later_controls_and_new_generation_is_isolated(runtime, control):
    """首次失败原因不被后续控制覆盖且新代次隔离"""
    plan = prepare(runtime)
    fail(runtime, plan)
    if control == "refresh":
        new = prepare(runtime, job(generation=2))
        assert not new["fenced"] and new["fence_reason"] == ""
        assert submit(runtime, new, 0)["status"] == "queued"
    else:
        result = runtime.owner.cancel_invocation(job=job(), surface="knowledge", deleted=control == "delete")
        assert result["fence_reason"] == "failed"
    assert fail(runtime, plan)["receipt"]["status"] == "failed"
    assert not runtime.executions.cancelled


@pytest.mark.parametrize("deleted", [False, True])
def test_cancel_before_prepare_and_delete_across_generations_remain_fenced(runtime, deleted):
    """准备前取消与删除封禁不影响其他主体和搜索"""
    result = runtime.owner.cancel_invocation(job=job(), surface="knowledge", deleted=deleted)
    assert result == {"receipt": None, "deleted": deleted, "fenced": True,
                      "fence_reason": "deleted" if deleted else "cancelled"}
    with pytest.raises(ServiceInvocationConflict):
        prepare(runtime)
    if deleted:
        with pytest.raises(ServiceInvocationConflict):
            prepare(runtime, job(generation=2))
    else:
        assert not prepare(runtime, job(generation=2))["fenced"]
    assert not prepare(runtime, job(actor="bob"))["fenced"]
    assert not prepare(runtime, job(search_id="another"))["fenced"]


def test_delete_fences_all_generations_even_when_requested_through_completed_history(runtime):
    """通过完成历史删除搜索仍封禁全部代次"""
    plan = prepare(runtime)
    for index in range(len(PROMPTS)):
        observe(plan, index, "completed")
    prepare(runtime, job(generation=2))
    runtime.owner.cancel_invocation(job=job(), surface="knowledge", deleted=True)
    for generation, status in ((1, "completed"), (2, "prepared")):
        receipt = runtime.owner.read_plan(job=job(generation=generation), surface="knowledge")
        assert receipt["fence_reason"] == "deleted" and not receipt["is_current_generation"]
        assert all(batch["status"] == status for batch in receipt["batches"])
    assert not runtime.executions.cancelled


@pytest.mark.parametrize("at", ["before_accept", "after_accept"])
def test_failure_racing_dispatch_cancels_only_the_reserved_execution_without_resubmission(runtime, monkeypatch, at):
    """失败与并发派发竞争仅清理原执行且不重复提交"""
    plan = prepare(runtime)
    entered, release = threading.Event(), threading.Event()
    original_submit = runtime.executions.submit

    def blocked(request):
        result = original_submit(request) if at == "after_accept" else None
        entered.set()
        assert release.wait(3)
        return result if at == "after_accept" else original_submit(request)

    monkeypatch.setattr(runtime.executions, "submit", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(submit, runtime, plan, 0)
        try:
            assert entered.wait(3)
            assert fail(runtime, plan)["fence_reason"] == "failed"
        finally:
            release.set()
        receipt = pending.result(timeout=3)
    assert receipt["fenced"] and receipt["fence_reason"] == "failed"
    assert runtime.executions.cancelled == [plan["batches"][0]["message_id"]]
    submit(runtime, plan, 0)
    submit(runtime, plan, 1)
    assert len(runtime.executions.submitted) == 1


def test_failure_retries_only_unacknowledged_cleanup_and_preserves_first_cause(runtime, monkeypatch):
    """失败清理异常仅重试未确认取消并保留失败原因"""
    plan = prepare(runtime)
    for index in range(2):
        submit(runtime, plan, index)
    observe(plan, 0, "completed")
    cancel = runtime.executions.cancel
    unavailable = Mock(side_effect=ConnectionError("Execution unavailable"))
    monkeypatch.setattr(runtime.executions, "cancel", unavailable)
    with pytest.raises(RuntimeError, match="cancellation is pending"):
        fail(runtime, plan)
    receipt = runtime.owner.read_plan(job=job(), surface="knowledge")
    assert receipt["fence_reason"] == "failed" and receipt["status"] == "failed"
    assert unavailable.call_count == 1
    monkeypatch.setattr(runtime.executions, "cancel", cancel)
    result = fail(runtime, plan)
    assert result["receipt"]["batches"][1]["cancel_delivered"]
    assert runtime.executions.cancelled == [plan["batches"][1]["message_id"]]
    fail(runtime, plan)
    assert len(runtime.executions.cancelled) == 1


def test_concurrent_finalizers_preserve_one_reason_and_terminal_outcomes(runtime):
    """并发终结保持幂等与终态批次不可变"""
    plan = prepare(runtime)
    observe(plan, 0, "completed")
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: fail(runtime, plan), range(8)))
    assert all(response == responses[0] for response in responses)
    assert all(response["fence_reason"] == "failed" for response in responses)
    observe(plan, 0, "cancelled")
    assert runtime.owner.read_plan(job=job(), surface="knowledge")["batches"][0]["status"] == "completed"
    assert not runtime.executions.cancelled


@pytest.mark.parametrize("invalid", ["actor", "surface", "search", "generation", "fingerprint", "inactive"])
def test_failure_requires_exact_active_job_and_frozen_plan_without_mutation(runtime, invalid):
    """失败终结严格校验原主体代次指纹及有效账号"""
    plan = prepare(runtime)
    selected = job()
    if invalid == "actor":
        selected["created_by"] = "bob"
    elif invalid == "surface":
        selected["surface"] = "paper"
    elif invalid == "search":
        selected["search_id"] = "another"
    elif invalid == "generation":
        selected["run_generation"] = 2
    elif invalid == "fingerprint":
        plan["plan_fingerprint"] = "wrong"
    elif invalid == "inactive":
        runtime.access.is_active.side_effect = lambda actor: actor == "bob"
    before = runtime.owner.read_plan(job=job(), surface="knowledge")
    error = {"surface": ValueError, "fingerprint": ServiceInvocationConflict, "inactive": PermissionError}.get(invalid, KeyError)
    with pytest.raises(error):
        fail(runtime, plan, selected)
    assert runtime.owner.read_plan(job=job(), surface="knowledge") == before
    assert not runtime.executions.submitted and not runtime.executions.cancelled


def test_fail_invocation_reaches_formal_mutation_rpc_and_preserves_conflict_contract(runtime, tmp_path, socket_rpc_authenticator):
    """失败终结本地与正式RPC一致并保留冲突异常"""
    plan = prepare(runtime)

    class Endpoint(socketserver.UnixStreamServer):
        dispatch = server.SocietyServiceServer.dispatch
        _domain_call = server.SocietyServiceServer._domain_call

    with Endpoint(str(tmp_path / "fail.sock"), server._LineageRequestHandler) as endpoint:
        endpoint.rpc_auth = socket_rpc_authenticator(endpoint.server_address)
        endpoint.read_pool = SimpleNamespace(workers=1, call=Mock(side_effect=AssertionError("mutation in read pool")))
        endpoint._finish_changes = Mock()
        worker = threading.Thread(target=endpoint.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            remote = SocietyServiceClient(endpoint.server_address, timeout=2).service_invocation
            assert operation_contract("service_invocation", "fail_invocation").mode == "write"
            with pytest.raises(ServiceInvocationConflict):
                remote.fail_invocation(job=job(), surface="knowledge", plan_fingerprint="wrong")
            result = remote.fail_invocation(job=job(), surface="knowledge", plan_fingerprint=plan["plan_fingerprint"])
            assert result == fail(runtime, plan)
            assert result["receipt"]["status"] == "failed" and result["fence_reason"] == "failed"
            assert endpoint.read_pool.call.call_count == 0
            assert not runtime.executions.submitted
        finally:
            endpoint.shutdown()
            worker.join(timeout=2)
            assert not worker.is_alive()

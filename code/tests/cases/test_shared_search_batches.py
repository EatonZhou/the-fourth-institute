from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal import shared_search_invocation as client
from management_portal.service_invocation_contract import batch_manifest
from management_portal.society_service import service_invocation_domain as domain
from tests.cases.test_shared_search_invocation import job, runtime


def prepare(selected=None, prompts=None):
    return client.prepare_shared_search_batches(
        job=selected or job(), surface="knowledge", prompts=prompts or ["first", "second", "third"],
    )


def run(plan, index=0, prompt="first", selected=None, **options):
    return client.run_shared_search_batch(
        job=selected or job(), surface="knowledge", prompt=prompt,
        batch_id=plan["batches"][index]["batch_id"], plan_fingerprint=plan["plan_fingerprint"],
        timeout_seconds=0.15, **options,
    )


def test_fixed_plan_dispatches_isolated_batches_and_only_all_complete_finishes(runtime):
    """固定计划按批次派发且全部完成后才标记完成"""
    plan = prepare()
    assert plan["batch_count"] == 3 and plan["status"] == "prepared"
    assert len({batch["message_id"] for batch in plan["batches"]}) == 3
    assert len({batch["resident_id"] for batch in plan["batches"]}) == 1
    first = run(plan)
    assert first["message"] == "first"
    parent = client.get_shared_search_invocation(job=job(), surface="knowledge")["receipt"]
    assert parent["status"] != "completed"
    run(plan, 1, "second")
    run(plan, 2, "third")
    parent = client.get_shared_search_invocation(job=job(), surface="knowledge")["receipt"]
    assert parent["status"] == "completed"
    assert {request.actor for request in runtime.executions.submitted} == {"alice"}
    assert {batch["run_generation"] for batch in plan["batches"]} == {1}
    assert {batch["search_id"] for batch in plan["batches"]} == {job()["search_id"]}


@pytest.mark.parametrize("changed", [["second", "first", "third"], ["first"], ["first", "changed", "third"]])
def test_parent_rejects_changed_order_size_or_input(runtime, changed):
    """同一计划拒绝批次顺序、数量或输入发生变化"""
    original = prepare()
    with pytest.raises(client.SharedSearchInvocationConflict):
        prepare(prompts=changed)
    assert prepare()["plan_fingerprint"] == original["plan_fingerprint"]
    assert not runtime.executions.submitted


def test_retries_and_concurrent_callers_reuse_successful_batch(runtime):
    """重试与并发调用复用成功批次且仅提交一次"""
    plan = prepare()
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda attempt: run(plan), range(5)))
    assert len({result["message_id"] for result in results}) == 1
    assert len(runtime.executions.submitted) == 1
    run(prepare())
    assert len(runtime.executions.submitted) == 1


def test_lost_ack_reuses_batch_and_next_batch_does_not_conflict(runtime):
    """提交确认丢失后复用原批次且后续批次正常派发"""
    plan = prepare()
    runtime.executions.lose_ack = True
    with pytest.raises(ConnectionError):
        run(plan)
    assert run(plan)["message"] == "first"
    assert run(plan, 1, "second")["message"] == "second"
    assert len(runtime.executions.submitted) == 2


@pytest.mark.parametrize("control", ["cancel", "delete", "refresh"])
def test_parent_control_cancels_inflight_and_fences_unsubmitted_batches(runtime, control, monkeypatch):
    """取消删除或刷新封锁未提交批次并取消在途执行"""
    plan = prepare()
    original_status = runtime.executions.status

    def running(execution_id, **options):
        return {**original_status(execution_id, **options), "status": "running", "result": {}}

    monkeypatch.setattr(runtime.executions, "status", running)
    first = plan["batches"][0]
    runtime.owner.submit_batch(job=job(), surface="knowledge", batch_id=first["batch_id"],
                              plan_fingerprint=plan["plan_fingerprint"], prompt="first")
    if control == "refresh":
        prepare(job(generation=2), ["new"])
    else:
        client.cancel_shared_search_invocation(job=job(), surface="knowledge", deleted=control == "delete")
    assert first["message_id"] in runtime.executions.cancelled
    with pytest.raises((client.SharedSearchInvocationFailed, client.SharedSearchInvocationConflict)):
        run(plan, 1, "second")
    parent = client.get_shared_search_invocation(job=job(), surface="knowledge")
    assert parent["receipt"]["fenced"] and parent["input_state"] == {}
    assert all(batch["fenced"] for batch in parent["receipt"]["batches"])
    assert len(runtime.executions.submitted) == 1


def test_cancel_before_plan_blocks_every_batch_but_not_other_creator(runtime):
    """计划创建前取消阻止所有批次且不影响其他创建者"""
    client.cancel_shared_search_invocation(job=job(), surface="knowledge")
    with pytest.raises(client.SharedSearchInvocationConflict):
        prepare()
    other = prepare(job("bob"))
    result = run(other, selected=job("bob"))
    assert result["receipt"]["created_by"] == "bob"


def test_cancel_during_submit_cancels_accepted_execution(runtime, monkeypatch):
    """提交期间取消检索会取消已接收的执行"""
    plan = prepare()
    original_submit = runtime.executions.submit

    def submit(request):
        client.cancel_shared_search_invocation(job=job(), surface="knowledge")
        return original_submit(request)

    monkeypatch.setattr(runtime.executions, "submit", submit)
    with pytest.raises(client.SharedSearchInvocationFailed):
        run(plan)
    assert runtime.executions.submitted[0].metadata["user_message_id"] in runtime.executions.cancelled
    assert len(runtime.executions.submitted) == 1


def test_completed_batch_is_not_reexecuted_when_later_batch_fails(runtime, monkeypatch):
    """后续批次失败后已完成批次不会重新执行"""
    plan = prepare()
    run(plan)
    original_status = runtime.executions.status

    def failure(execution_id, **options):
        snapshot = original_status(execution_id, **options)
        if execution_id == plan["batches"][1]["message_id"]:
            snapshot["status"] = snapshot["result"]["status"] = "failed"
        return snapshot

    monkeypatch.setattr(runtime.executions, "status", failure)
    with pytest.raises(client.SharedSearchInvocationFailed):
        run(plan, 1, "second")
    run(plan)
    assert len(runtime.executions.submitted) == 2
    assert client.get_shared_search_invocation(job=job(), surface="knowledge")["receipt"]["status"] != "completed"


def test_invalid_plan_or_batch_fails_before_execution(runtime):
    """计划指纹、批次或输入无效时拒绝派发执行"""
    plan = prepare()
    with pytest.raises(client.SharedSearchInvocationConflict):
        run(plan, prompt="replacement")
    with pytest.raises(client.SharedSearchInvocationConflict):
        run({**plan, "plan_fingerprint": "0" * 64})
    with pytest.raises((KeyError, ValueError)):
        run({**plan, "batches": [{"batch_id": "outside-plan"}]})
    assert not runtime.executions.submitted


def test_manifest_cannot_smuggle_prompts_or_arbitrary_batch_identity(runtime):
    """批次清单拒绝夹带提示词等未声明字段"""
    batches = batch_manifest(job(), ["first", "second"])
    batches[0]["prompt"] = "not a manifest field"
    with pytest.raises(ValueError):
        runtime.owner.prepare_batches(job=job(), surface="knowledge", batches=batches)


def test_callback_failure_preserves_prepared_receipt_without_dispatch(runtime):
    """分配回调失败时保留预备回执且不派发执行"""
    plan = prepare()

    def reject(receipt):
        receipt["message_id"] = "changed"
        raise RuntimeError("JobStore write failed")

    with pytest.raises(RuntimeError, match="JobStore"):
        run(plan, on_assigned=reject)
    assert not runtime.executions.submitted
    assert run(plan)["message_id"] == plan["batches"][0]["message_id"]


def test_cancellation_failure_preserves_fence_and_attempts_remaining_batches(runtime, monkeypatch):
    """部分批次取消失败时保留封锁并继续取消其余批次"""
    plan = prepare()
    for index, prompt in enumerate(["first", "second", "third"]):
        runtime.owner.submit_batch(job=job(), surface="knowledge", batch_id=plan["batches"][index]["batch_id"],
                                  plan_fingerprint=plan["plan_fingerprint"], prompt=prompt)
    original_cancel = runtime.executions.cancel

    def cancel(execution_id, **options):
        if execution_id == plan["batches"][0]["message_id"]:
            raise ConnectionError("temporary cancellation outage")
        return original_cancel(execution_id, **options)

    monkeypatch.setattr(runtime.executions, "cancel", cancel)
    with pytest.raises(RuntimeError, match="cancellation is pending"):
        client.cancel_shared_search_invocation(job=job(), surface="knowledge")
    assert set(runtime.executions.cancelled) == {batch["message_id"] for batch in plan["batches"][1:]}
    assert domain._store().get(job(), "knowledge")["fenced"]
    monkeypatch.setattr(runtime.executions, "cancel", original_cancel)
    client.cancel_shared_search_invocation(job=job(), surface="knowledge")
    assert set(runtime.executions.cancelled) == {batch["message_id"] for batch in plan["batches"]}


def test_pending_batch_rechecks_approved_route_without_reexecuting_success(runtime):
    """待执行批次复核批准路由且已成功批次仍可复用"""
    plan = prepare()
    first = run(plan)
    runtime.registry.resolve.side_effect = PermissionError("binding withdrawn")
    assert run(plan)["message_id"] == first["message_id"]
    with pytest.raises(PermissionError):
        run(plan, 1, "second")
    assert len(runtime.executions.submitted) == 1


def test_refresh_fence_survives_late_completion_observation(runtime, monkeypatch):
    """刷新后迟到的完成观察不会解除旧计划封锁"""
    plan = prepare(prompts=["first"])
    original_status = runtime.executions.status
    refreshed = False

    def status(execution_id, **options):
        nonlocal refreshed
        snapshot = original_status(execution_id, **options)
        if not refreshed:
            refreshed = True
            prepare(job(generation=2), ["next generation"])
        return snapshot

    monkeypatch.setattr(runtime.executions, "status", status)
    with pytest.raises(client.SharedSearchInvocationFailed):
        run(plan)
    previous = client.get_shared_search_invocation(job=job(), surface="knowledge")
    assert previous["receipt"]["fenced"] and previous["input_state"] == {}
    assert previous["receipt"]["status"] == "superseded"
    assert len(runtime.executions.submitted) == 1


def test_rpc_preserves_plan_conflict_type():
    """计划冲突经 RPC 传输后保留异常类型"""
    from management_portal.society_service.server import _error_payload
    from management_portal.society_service.transport import SocietyServiceClient
    from management_portal.society_service.service_invocation_store import ServiceInvocationConflict
    from management_portal.service_invocation_contract import ServiceInvocationConflict as ContractConflict

    assert ServiceInvocationConflict is ContractConflict is client.SharedSearchInvocationConflict
    with pytest.raises(client.SharedSearchInvocationConflict, match="frozen plan changed"):
        SocietyServiceClient._raise_remote_error(_error_payload(ServiceInvocationConflict("frozen plan changed")))


def test_concurrent_plan_preparation_preserves_all_reserved_identities(runtime):
    """并发准备计划复用同一指纹和全部预留消息标识"""
    with ThreadPoolExecutor(max_workers=4) as workers:
        plans = list(workers.map(lambda attempt: prepare(), range(5)))
    assert len({plan["plan_fingerprint"] for plan in plans}) == 1
    assert len({tuple(batch["message_id"] for batch in plan["batches"]) for plan in plans}) == 1
    assert not runtime.executions.submitted

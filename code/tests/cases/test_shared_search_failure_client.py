from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import shared_search_invocation as client


JOB = {"created_by": "alice", "surface": "knowledge", "search_id": "search-client", "run_generation": 1}


def test_failure_wrapper_preserves_owner_receipt_and_optional_fingerprint(monkeypatch, subtests):
    """共享检索失败客户端保留回执与计划指纹

    验证失败委派在五种fence原因下保留权威父子回执，支持默认空指纹与显式指纹。

    :risk: no-external-side-effects
    """
    owner = Mock()
    monkeypatch.setattr(client, "get_society_service", lambda: SimpleNamespace(service_invocation=owner))
    for reason in ("failed", "cancelled", "deleted", "superseded", "unknown"):
        with subtests.test(reason=reason):
            response = {"receipt": {"fenced": True, "fence_reason": reason, "batches": [
                {"message_id": "execution-completed", "status": "completed"},
            ]}, "fenced": True, "fence_reason": reason}
            owner.fail_invocation.return_value = response
            for options in ({}, {"plan_fingerprint": ""}, {"plan_fingerprint": "frozen-plan"}):
                assert client.fail_shared_search_invocation(
                    job=MappingProxyType(JOB), surface="knowledge", **options,
                ) is response
                owner.fail_invocation.assert_called_with(
                    job=JOB, surface="knowledge", plan_fingerprint=options.get("plan_fingerprint", ""),
                )
                assert owner.fail_invocation.call_args.kwargs["job"] is not JOB
            owner.cancel_invocation.assert_not_called()


def test_failure_wrapper_propagates_owner_error_without_cancel_fallback(monkeypatch, subtests):
    """共享检索失败客户端不回退取消

    验证权限拒绝、计划冲突与响应丢失原样传播，不以取消或重试替代失败收尾。

    :risk: no-external-side-effects
    """
    owner = Mock()
    monkeypatch.setattr(client, "get_society_service", lambda: SimpleNamespace(service_invocation=owner))
    for failure in (PermissionError("denied"), client.SharedSearchInvocationConflict("plan mismatch"),
                    ConnectionError("response lost")):
        with subtests.test(error=type(failure).__name__):
            owner.fail_invocation.side_effect = failure
            with pytest.raises(type(failure)) as raised:
                client.fail_shared_search_invocation(job=JOB, surface="knowledge")
            assert raised.value is failure
            owner.cancel_invocation.assert_not_called()
    assert owner.fail_invocation.call_count == 3


def test_fenced_batch_keeps_reason_at_each_observation_boundary(monkeypatch, subtests):
    """共享检索受限回执保留真实原因

    验证派发前、派发后与等待后观察到fence时保留权威原因，不使用取消文字覆盖失败语义。

    :risk: no-external-side-effects
    """
    prompt = "authorized input"
    receipt = {"message_id": "msg_original", "created_by": "alice", "status": "running",
               "resident_id": "resident_123456789abc", "lineage_id": "lineage_123456789abc",
               "input_fingerprint": client.input_fingerprint(JOB, prompt), "is_current_generation": True}
    for boundary in ("before_dispatch", "after_dispatch", "after_wait"):
        with subtests.test(boundary=boundary):
            for reason in ("failed", "cancelled", "deleted", "superseded", "unknown"):
                owner, wait = Mock(), Mock(return_value={"message_id": "msg_original", "message": "reply"})
                monkeypatch.setattr(client, "get_society_service", lambda: SimpleNamespace(service_invocation=owner))
                monkeypatch.setattr(client, "resume_and_wait", wait)
                fenced = {**receipt, "fenced": True, "fence_reason": reason}
                owner.get_batch.side_effect = [
                    {"receipt": fenced if boundary == "before_dispatch" else receipt},
                    {"receipt": fenced, "input_state": {"state": "completed"}},
                ]
                owner.submit_batch.return_value = fenced if boundary == "after_dispatch" else receipt
                with pytest.raises(client.SharedSearchInvocationFailed) as raised:
                    client.run_shared_search_batch(job=JOB, surface="knowledge", prompt=prompt,
                                                   batch_id="batch-000001", plan_fingerprint="frozen-plan")
                assert raised.value.receipt == fenced, reason
                assert raised.value.message_id == "msg_original"
                assert "cancel" not in str(raised.value)
                assert owner.submit_batch.call_count == (boundary != "before_dispatch")
                assert wait.call_count == (boundary == "after_wait")
                owner.cancel_invocation.assert_not_called()

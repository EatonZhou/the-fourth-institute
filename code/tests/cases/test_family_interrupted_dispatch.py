"""Interrupted ISA receipts reconcile only a frozen Family task identity.

:module: lineage.team
:design: decision_b77fde81e7a0
"""
import pytest

from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import resident_intake_domain
from tests.cases.test_family_task_idempotency import real_family, _snapshot
from tests.cases.isa_test_support import ok
from tests.cases.test_family_tasks import charters, configured_family, future_creation_store, lineage  # noqa: F401


class Crash(BaseException):
    pass


@pytest.mark.parametrize("boundary", ["before_intent", "after_intent", "after_intake", "after_receipt", "cancelled"])
def test_interrupted_task_reconciles_frozen_intent_once(real_family, monkeypatch, boundary):
    _, block = real_family
    family = block["family"]
    mid = "msg_60000000000000000000000000000001"
    call = InstructionCall.from_payload({"instruction": "DispatchFamilyTask", "idempotency_key": "interrupted-summary",
        "arguments": {"resident_id": family["facade"], "trigger": "summary", "message_id": mid}})
    context = InstructionContext(family["steward"], "web")
    kernel = routing.kernel_for("lineage", required=True)
    original = resident_intake_domain.deliver_resident_message_content

    def interrupt(*args, **kwargs):
        if boundary in {"after_intake", "cancelled"}:
            original(*args, **kwargs)
        raise Crash()

    with monkeypatch.context() as patch:
        if boundary == "before_intent":
            patch.setattr("management_portal.society_service.family_task_dispatches.dispatch", lambda *a, **k: (_ for _ in ()).throw(Crash()))
        elif boundary == "after_receipt":
            patch.setattr(kernel.store, "finish", lambda *a, **k: (_ for _ in ()).throw(Crash()))
        else:
            patch.setattr(resident_intake_domain, "deliver_resident_message_content", interrupt)
        with pytest.raises(Crash):
            kernel.execute(call, context)
    iid = kernel.store.find_instruction_id(caller=context.caller, instruction=call.instruction, request_key=call.idempotency_key)
    kernel.store.settle_interrupted(now="2026-11-13T00:00:00Z")
    if boundary == "cancelled":
        ok("CancelInput", {"resident_id": family["steward"], "message_id": mid})
    if boundary in {"after_intake", "after_receipt", "cancelled"}:
        before = _snapshot(family, mid)
        monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", lambda *a, **k: pytest.fail("accepted input must not be delivered again"))
    result = kernel.execute(call, context, instruction_id=iid)
    if boundary == "before_intent":
        assert result.status == "failed" and result.error["commit_state"] == "unknown"
        return
    assert result.status == "committed", result.error
    assert result.instruction_id == iid and result.result["message_id"] == mid
    after = _snapshot(family, mid)
    if boundary in {"after_intake", "after_receipt", "cancelled"}:
        assert after == before
    assert len(after["accepted"]) == 1
    assert kernel.execute(call, context).result == result.result


def test_recovery_never_redirects_frozen_task_to_replacement_role(real_family, monkeypatch):
    import copy
    from management_portal.society_service import family_task_dispatches, family_tasks

    body, block = real_family
    family = block["family"]
    mid = "msg_60000000000000000000000000000002"
    args = {"resident_id": family["facade"], "trigger": "summary", "message_id": mid}
    ok("DispatchFamilyTask", args, actor=family["steward"])
    before = _snapshot(family, mid)
    changed = copy.deepcopy(block)
    changed["family"]["steward"] = family["router"]
    monkeypatch.setattr(family_tasks, "context", lambda _facade: (body, changed))
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", lambda *a, **k: pytest.fail("must not redirect an interrupted task"))
    assert family_task_dispatches.recover_dispatch(family["facade"], "summary", family["facade"], message_id=mid) is None
    assert _snapshot(family, mid) == before


def test_task_without_explicit_message_identity_is_not_reconciled():
    from management_portal.society_service.family_task_dispatches import recover_dispatch
    assert recover_dispatch("not-looked-up", "summary", "actor") is None

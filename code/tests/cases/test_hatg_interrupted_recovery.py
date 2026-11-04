"""Only explicitly registered owner reconciliation can settle interrupted writes."""
import pytest
from tests.cases.test_hatg_isa_kernel import kernel, fake, scheduler_call, CONTEXT  # noqa: F401
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service.lineage_program_effect_execution import _CallAttempt


def interrupted(kernel):
    call = scheduler_call(idempotency_key="owner-reconciliation")
    first = kernel.execute(call, CONTEXT)
    kernel.store.database.write(lambda c: c.execute(
        "UPDATE instruction_executions SET status='executing', error_json='null' WHERE instruction_id=%s",
        (first.instruction_id,)))
    kernel.store.settle_interrupted(now="2026-11-04T00:00:00Z")
    return call, first


def test_owner_reconciliation_is_serialized_and_uses_original_id(kernel, fake):
    call, first = interrupted(kernel)
    calls = []
    def reconcile(inv):
        calls.append(inv.instruction_id)
        return {"account_id": "original-account"}
    kernel.binding.recover_interrupted[call.instruction] = reconcile
    with kernel.store.active_execution(first.instruction_id):
        pending = kernel.execute(call, CONTEXT)
    assert pending.error["details"]["reason"] == "interrupted_recovery_pending"
    attempt = _CallAttempt(CONTEXT, "")
    assert attempt.executed_entry(pending)["status"] == "unresolved"
    assert not attempt.final_receipt
    assert not calls
    result = kernel.execute(call, CONTEXT, instruction_id=first.instruction_id)
    assert result.status == "committed" and result.instruction_id == first.instruction_id
    assert calls == [first.instruction_id]
    assert kernel.execute(call, CONTEXT).result == result.result
    assert len(fake.calls) == 1 and calls == [first.instruction_id]


def test_outage_preserves_unknown_and_later_reconciles(kernel):
    call, first = interrupted(kernel)
    kernel.binding.recover_interrupted[call.instruction] = lambda inv: (_ for _ in ()).throw(OSError("outage"))
    pending = kernel.execute(call, CONTEXT)
    assert pending.error["commit_state"] == "unknown" and pending.error["retryable"]
    assert kernel.store.get(first.instruction_id)["error"]["details"]["reason"] == "interrupted"
    kernel.binding.recover_interrupted[call.instruction] = lambda inv: {"account_id": "settled"}
    assert kernel.execute(call, CONTEXT).status == "committed"


def test_recovery_still_checks_permission_and_fingerprint(kernel):
    call, first = interrupted(kernel)
    def forbidden(*args, **kwargs):
        raise InstructionError("instruction.forbidden", "revoked")
    calls = []
    kernel.binding.recover_interrupted[call.instruction] = lambda inv: calls.append(inv)
    changed = scheduler_call(idempotency_key="owner-reconciliation")
    changed.arguments["account_id"] = "other-account"
    assert kernel.execute(changed, CONTEXT).error["code"] == "instruction.idempotency_conflict"
    # Emulate permission revoked since the original dispatch.
    kernel._authorize_call = forbidden
    assert kernel.execute(call, CONTEXT).error["code"] == "instruction.forbidden"
    assert not calls

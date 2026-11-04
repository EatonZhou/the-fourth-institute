"""Ordinary handoffs reconcile the original delivery after owner interruption."""
import pytest

from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionCall, InstructionContext
from management_portal.society_service import family_facade, family_quota, outward_messages
from management_portal.society_service import resident_conversation_stream, resident_message_contract
from management_portal.society_service.resident_record_domain import find_resident
from tests.cases.test_family_quota import quota_family, team, owners, ACTOR, _account  # noqa: F401


@pytest.mark.parametrize("boundary", ["before_intake", "after_intake", "after_link", "cancelled"])
def test_same_call_recovers_once_at_each_commit_boundary(quota_family, monkeypatch, boundary):
    f = quota_family
    f.accounts[:] = [_account("one", 90)]
    repo = resident_conversation_stream.get_repository()
    kernel = routing.kernel_for("lineage", required=True)
    arguments = {"resident_id": f.facade, "message_id": f.message, "member_id": f.member}
    call = InstructionCall.from_payload({"instruction": "RelayFamilyMessage", "arguments": arguments,
                                        "idempotency_key": "interrupted-handoff"})
    context = InstructionContext(ACTOR, "web")
    original = outward_messages.deliver_to_resident
    delivered = []
    class Crash(BaseException):
        pass
    def interrupt(*args, **kwargs):
        if boundary in {"before_intake", "cancelled"}:
            raise Crash()
        result = original(*args, **kwargs)
        delivered.append(result["message_id"])
        raise Crash()
    with monkeypatch.context() as patch:
        if boundary == "after_link":
            patch.setattr(kernel.store, "finish", lambda *a, **k: (_ for _ in ()).throw(Crash()))
        else:
            patch.setattr(outward_messages, "deliver_to_resident", interrupt)
        member_mid = resident_message_contract.derived_message_identity(
            "family-relay", f.facade, f.message, f.member)
        with pytest.raises(Crash):
            kernel.execute(call, context)
    instruction_id = kernel.store.find_instruction_id(caller=ACTOR, instruction=call.instruction,
                                                      request_key=call.idempotency_key)
    kernel.store.settle_interrupted(now="2026-11-04T00:00:00Z")
    if boundary == "cancelled":
        repo.set_input_state(f.facade, f.message, "cancelled")
    if boundary in {"after_intake", "after_link"}:
        f.accounts[:] = [_account("one", 1)]
        monkeypatch.setattr(outward_messages, "deliver_to_resident",
            lambda *a, **k: pytest.fail("accepted input must be read, not delivered again"))
    # Effect recovery supplies the same execution ID, ordinary API retries
    # may supply a fresh ID. Both must keep the same original receipt.
    result = kernel.execute(call, context, instruction_id=instruction_id)
    assert result.status == "committed", result.error
    assert result.instruction_id == instruction_id
    if boundary == "cancelled":
        assert result.result["status"] == "withdrawn"
        assert repo.message(f.member, member_mid) is None
    else:
        assert result.result["message_id"] == member_mid
        assert repo.message(f.member, member_mid) is not None
        link = family_facade.relay_links(find_resident(f.facade))[f.message]
        assert (link["resident_id"], link["message_id"]) == (f.member, member_mid)
    again = kernel.execute(call, context)
    assert again.status == "committed" and again.instruction_id == instruction_id
    assert again.result == result.result


@pytest.mark.parametrize("flag", ["reassign", "recover", "notice", "quota_reviewed", "scope_reviewed", "scope_gap"])
def test_recovery_does_not_reexecute_other_relay_protocols(flag):
    from management_portal.society_service.isa import recover_relay
    from types import SimpleNamespace
    inv = SimpleNamespace(argument=lambda key: {flag: True, "member_id": "member"}.get(key))
    assert recover_relay(inv) is None

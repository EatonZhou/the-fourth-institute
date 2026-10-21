from __future__ import annotations


import pytest

from management_portal.society_service import (
    invariant_locks,
    lineage_domain,
    lineage_program_effect_execution,
    resident_conversation_stream,
    resident_domain,
    resident_message_contract,
    resident_project_domain,
    resident_protocol,
)
from management_portal.society_service.lineage_program_effect_rules import effect_idempotency_key
from tests.cases.isa_test_support import reset_kernels, run
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401 - fixture
    _new_session_payload,
    future_creation_store,
)
from tests.cases.effect_script_support import effect_config, lower_effect_call


REAL_DELIVER_RESIDENT_INPUT = resident_protocol.deliver_resident_input


@pytest.fixture
def delivery_resident(future_creation_store, monkeypatch):
    monkeypatch.setattr(resident_project_domain, "_project_root_for_id", lambda _: future_creation_store)
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", REAL_DELIVER_RESIDENT_INPUT)
    reset_kernels()
    record = resident_domain.register_resident(_new_session_payload("Delivery receipt"), "sample_admin")
    resident_protocol.change_resident_state(
        record["resident_id"], "sample_admin", state_domain="intake", transition="close",
    )
    yield record
    reset_kernels()


@pytest.mark.parametrize("explicit_id", ["", "msg_0123456789abcdef0123456789abcdef"])
def test_formal_delivery_receipt_is_available_before_outer_unlock(delivery_resident, explicit_id):
    """外层 Lineage 锁释放前 DeliverMessage 已返回正式回执，重放得到同一消息 ID"""
    resident_id = delivery_resident["resident_id"]
    lineage_id = delivery_resident["lineage_id"]
    key = "delivery-before-unlock"
    arguments = {
        "resident_id": resident_id,
        "message": {"role": "user", "content_parts": [{"type": "text", "text": "One exact input"}]},
        "activation_policy": "enqueue_and_trigger",
    }
    if explicit_id:
        arguments["message"]["message_id"] = explicit_id
    expected_id = explicit_id or resident_message_contract.derived_message_identity(
        "isa-deliver", resident_id, "sample_admin", key,
    )
    with invariant_locks.lineage_scope(lineage_id, operation="test.delivery.receipt"):
        first = run("DeliverMessage", arguments, key=key, entry="lineage").raise_for_error()
        assert first["resident_id"] == resident_id
        assert resident_message_contract.delivered_message_id(first) == expected_id
        replay = run("DeliverMessage", arguments, key=key, entry="lineage")
        assert replay.replayed
        assert replay.result["message_id"] == expected_id
        if explicit_id:
            repeated = run("DeliverMessage", arguments, key="same-message-new-instruction", entry="lineage").raise_for_error()
            assert resident_message_contract.delivered_message_id(repeated) == expected_id
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, expected_id)["state"] == "queued"
    assert [item["message_id"] for item in repository.input_states(resident_id)] == [expected_id]


def test_deliver_message_effect_uses_the_formal_receipt_under_the_outer_lock_and_replays_once(delivery_resident):
    """DeliverMessage Effect 在外层 Lineage 锁内拿到正式回执的消息 ID，重放只投递一次"""
    from management_common.principal import OperationIdentity
    resident_id = delivery_resident["resident_id"]
    lineage_id = delivery_resident["lineage_id"]
    primitives = lineage_domain.lineage_primitives
    lineage = lineage_domain.get_lineage(lineage_id, "sample_admin")
    effect_id = "effect_delivery_receipt"
    plan = {"effects": {effect_id: {**effect_config("DeliverMessage", {"resident_id": {"from": "endpoint.target"}}), "endpoints": {"target": resident_id}}}}
    invocation = primitives.make_effect_invocation(
        primitives.make_event("manual.triggered", identity=OperationIdentity.for_actor("sample_admin").to_dict()),
        arguments={"prompt": "Exactly one Effect input"},
        source_wire_id="wire_delivery_receipt",
    )
    expected_id = resident_message_contract.derived_message_identity(
        "isa-deliver", resident_id, "sample_admin", effect_idempotency_key(lineage_id, effect_id, invocation),
    )
    with invariant_locks.lineage_scope(lineage_id, operation="test.effect.receipt"):
        first = lineage_program_effect_execution._execute_program_effect_component(
            lineage, plan, effect_id, invocation, "sample_admin",
            host=lineage_domain._program_event_authority(),
        )
        assert first["status"] == "executed", first.get("error")
        assert [item["message_id"] for item in first["resident_invocations"]] == [expected_id]
        assert first["typed_outputs"]["message_id"] == expected_id
        assert first["typed_outputs"]["resident_id"] == resident_id
        replay = lineage_program_effect_execution._execute_program_effect_component(
            lineage, plan, effect_id, invocation, "sample_admin",
            host=lineage_domain._program_event_authority(),
        )
        assert replay["replayed"] and replay["instruction_id"] == first["instruction_id"]
        assert replay["typed_outputs"]["message_id"] == expected_id
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, expected_id)["state"] == "queued"
    assert [item["message_id"] for item in repository.input_states(resident_id)] == [expected_id]


@pytest.mark.parametrize("response", [
    None,
    {"state": {"active_message_id": "msg_unrelated"}},
    {"messages": [{"message_id": "msg_unrelated"}]},
])
def test_delivery_identity_does_not_guess_from_projection(response):
    """回执中没有明确的消息 ID 时，不从状态或消息列表推测投递的消息"""
    assert resident_message_contract.delivered_message_id(response) == ""


def test_delivery_identity_rejects_conflicting_acknowledgements():
    """回执中两处消息 ID 不一致时报错"""
    with pytest.raises(ValueError, match="conflicting message IDs"):
        resident_message_contract.delivered_message_id({
            "message_id": "msg_0123456789abcdef",
            "message": {"message_id": "msg_fedcba9876543210"},
        })


def test_effect_output_uses_the_same_acknowledged_message_identity():
    """DeliverMessage Effect 的输出使用回执确认的同一消息 ID"""
    response = {"resident_id": "resident_aaaaaaaaaaaa", "message_id": "msg_0123456789abcdef"}
    assert lineage_program_effect_execution.effect_outputs("DeliverMessage", response)["message_id"] == (
        resident_message_contract.delivered_message_id(response)
    ) == "msg_0123456789abcdef"


def test_delivery_receipt_survives_input_moving_between_mailbox_and_chat(monkeypatch):
    """输入在 Mailbox 与 Chat 之间移动时，投递回执仍给出已接收的消息与排队状态

    :module: lineage.hatg
    :aspects: functional, behavior
    :design: decision_44cf587c1721
    """
    from types import SimpleNamespace
    from management_portal.society_service import resident_intake_domain
    accepted = {"message_id": "msg_accepted", "role": "user", "content_parts": [{"type": "text", "text": "route this request"}]}
    reads = []
    def message(*args):
        reads.append(args)
        return dict(accepted) if len(reads) == 1 else None
    repository = SimpleNamespace(
        current_projection=lambda _: {"state": {"work_state": "queued"}, "cursor": 5},
        message=message,
        public_message=lambda *a: (_ for _ in ()).throw(KeyError("msg_accepted")),
        input_state=lambda *a: {"message_id": "msg_accepted", "state": "queued"},
    )
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    receipt = resident_intake_domain._resident_message_delivery_result("resident_target", "msg_accepted")
    assert receipt["message"] == accepted
    assert receipt["input_state"]["state"] == "queued"
    assert len(reads) == 1

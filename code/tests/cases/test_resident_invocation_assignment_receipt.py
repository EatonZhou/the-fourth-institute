import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import isa_client, lineage_workflows, resident_handoff

IDENTITY = {"resident_id": "resident_123456789abc", "lineage_id": "lineage_123456789abc"}
WORKFLOW = {**IDENTITY, "event_source_id": "event_source_isolated", "effect_id": "effect_isolated"}


def expected_receipt(workflow_binding):
    """The prepared submission handle, saved before anything is submitted."""
    if workflow_binding is None:
        return {**IDENTITY, "request_id": "msg_assignment_test", "instruction": "DeliverMessage",
                "idempotency_key": "invocation:msg_assignment_test", "submission_state": "pending"}
    digest = hashlib.sha256(b"msg_assignment_test").hexdigest()
    return {**workflow_binding, "request_id": "msg_assignment_test", "instruction": "TriggerEvent",
            "idempotency_key": f"workflow:event_source_isolated:{digest}", "submission_state": "pending"}


@pytest.fixture
def isolated_assignment(monkeypatch):
    identity = dict(IDENTITY)
    ensure = Mock(return_value=identity)
    delivery = Mock(return_value={"message_id": "msg_assignment_test"})
    service = SimpleNamespace(resident=SimpleNamespace())
    monkeypatch.setattr(isa_client, "call", lambda actor, instruction, arguments, *, key="": delivery(
        {"instruction": instruction, "arguments": arguments, "idempotency_key": key}, actor=actor))
    trigger = Mock(return_value={**identity, "message_id": "msg_workflow_test"})
    workflow_factory = Mock(return_value=SimpleNamespace(trigger=trigger))
    # Preparing a workflow handle is pure; only constructing the bindings store is isolated.
    workflow_factory.prepare = lineage_workflows.WorkflowBindings.prepare
    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: service)
    monkeypatch.setattr(resident_handoff, "ensure_invocation_resident", ensure)
    monkeypatch.setattr(lineage_workflows, "WorkflowBindings", workflow_factory)
    return SimpleNamespace(
        identity=identity,
        ensure=ensure,
        delivery=delivery,
        trigger=trigger,
        workflow_factory=workflow_factory,
    )


@pytest.mark.parametrize("preparation_failure", [None, "busy", "in_progress", "forbidden", "cancelled"])
@pytest.mark.parametrize("workflow_binding", [None, WORKFLOW])
@pytest.mark.parametrize("error_type", [RuntimeError, PermissionError])
def test_assignment_callback_failure_never_delivers(
    isolated_assignment, workflow_binding, error_type, preparation_failure, monkeypatch
):
    """分派回执保存失败或准备阶段被拒绝、取消时，调用不投递消息也不触发工作流"""
    assignment = isolated_assignment
    failure = error_type("assignment persistence failed")
    assigned = Mock(side_effect=failure)
    stopped = False
    if preparation_failure:
        from management_portal.society_service import SocietyServiceBusy
        from management_portal.hatg.contracts import InstructionError

        transient = (SocietyServiceBusy("preparation response lost")
                     if preparation_failure in {"busy", "cancelled"} else
                     PermissionError("preparation denied") if preparation_failure == "forbidden" else
                     InstructionError("instruction.in_progress", "preparation executing", http_status=409))
        assignment.ensure.side_effect = [transient, assignment.identity]
        def wait(_delay):
            nonlocal stopped
            stopped = preparation_failure == "cancelled"
        monkeypatch.setattr(resident_handoff.time, "sleep", wait)

    expected_error = (PermissionError if preparation_failure == "forbidden" else
                      InterruptedError if preparation_failure == "cancelled" else error_type)
    with pytest.raises(expected_error) as raised:
        resident_handoff.submit_invocation(
            actor="test-actor",
            message="isolated fixture only",
            resident_scope="assignment-regression",
            message_id="msg_assignment_test",
            on_assigned=assigned,
            workflow_binding=workflow_binding,
            cancellation_requested=lambda: stopped,
        )

    preparation_stopped = preparation_failure in {"forbidden", "cancelled"}
    if preparation_stopped:
        assigned.assert_not_called()
    else:
        assert raised.value is failure
        assigned.assert_called_once_with(expected_receipt(workflow_binding))
    assert assignment.ensure.call_count == (2 if preparation_failure and not preparation_stopped else 1)
    assert all(call == assignment.ensure.call_args_list[0] for call in assignment.ensure.call_args_list)
    assignment.delivery.assert_not_called()
    assignment.workflow_factory.assert_not_called()
    assignment.trigger.assert_not_called()


@pytest.mark.parametrize("workflow_binding", [None, WORKFLOW])
def test_assignment_receipt_is_saved_before_delivery(
    isolated_assignment, workflow_binding
):
    """调用先保存分派回执，再投递 DeliverMessage 或触发工作流"""
    assignment = isolated_assignment
    sequence = []

    def save_receipt(identity):
        assert identity == expected_receipt(workflow_binding)
        assignment.delivery.assert_not_called()
        assignment.trigger.assert_not_called()
        sequence.append("saved")
        identity["resident_id"] = "resident_ffffffffffff"

    def deliver(payload, *, actor):
        assert sequence == ["saved"]
        assert actor == "test-actor"
        assert payload["instruction"] == "DeliverMessage"
        assert payload["arguments"]["resident_id"] == assignment.identity["resident_id"]
        sequence.append("delivered")
        return {"message_id": "msg_assignment_test"}

    def trigger(binding, **kwargs):
        assert sequence == ["saved"]
        assert binding == workflow_binding
        assert kwargs["actor"] == "test-actor"
        sequence.append("delivered")
        return {**assignment.identity, "message_id": "msg_workflow_test"}

    assignment.delivery.side_effect = deliver
    assignment.trigger.side_effect = trigger
    result = resident_handoff.submit_invocation(
        actor="test-actor",
        message="isolated fixture only",
        resident_scope="assignment-regression",
        message_id="msg_assignment_test",
        on_assigned=save_receipt,
        workflow_binding=workflow_binding,
    )

    assert sequence == ["saved", "delivered"]
    assert result["resident_id"] == "resident_123456789abc"
    if workflow_binding is None:
        assignment.delivery.assert_called_once()
        assignment.workflow_factory.assert_not_called()
    else:
        assignment.delivery.assert_not_called()
        assignment.trigger.assert_called_once()

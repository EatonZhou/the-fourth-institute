"""Addressed Family requests must pass the arrival Effect's routing gate.

:module: lineage.hatg
"""
from copy import deepcopy
from pathlib import Path

import pytest

from management_portal.society_service import family_definition
from management_portal.society_service.lineage_effect_script import CallResult, Interpreter, parse


FACADE = "resident_bbbbbbbbbbbb"
MEMBER = "resident_aaaaaaaaaaaa"
STEWARD = "resident_cccccccccccc"
MESSAGE = "msg_" + "1" * 32
EVENT = "event_1234567890abcdef"
REASONS = {
    "direct_member_id": "按发送者的直连目标转交",
    "quoted_member_id": "引用了这个成员的消息，直接交回",
    "continued_member_id": "同一 Resident 接着发来的请求，交给承接它上一条请求的成员",
}


def run_route(branch, status, *, cancelled=False, denied=False):
    calls = []
    inputs = {
        "arguments": {"steward": STEWARD, "members": [MEMBER]},
        "event": {"id": EVENT, "payload": {
            "resident_id": FACADE, "message_id": MESSAGE, "relay_depth": 0, branch: MEMBER,
        }},
    }
    before = deepcopy(inputs)

    def execute(request):
        calls.append((request.instruction, request.arguments))
        if request.instruction == "GetRelayContext":
            return CallResult(True, {"request": {"author_id": "original_user", "input_state": {
                "state": "cancelled" if cancelled else "queued"}}})
        if request.instruction == "CheckRelayTarget":
            if denied:
                return CallResult(False, {}, {"code": "instruction.no_permit", "message": "denied"})
            return CallResult(True, {"status": status, "reason": "没有可用账号", "remaining_percent": 3})
        if request.instruction == "DeliverMessage":
            return CallResult(True, {"message_id": request.arguments["message"]["message_id"]})
        if request.instruction == "RelayMessage":
            return CallResult(True, {"status": "relayed"})
        raise AssertionError(request.instruction)

    result = Interpreter(parse(family_definition.FAMILY_ROUTER_SCRIPT), inputs, execute).run()
    assert inputs == before
    return result, calls


@pytest.mark.parametrize("branch", REASONS)
@pytest.mark.parametrize("status", ["quota_low", "unavailable", "ready", "unknown"])
def test_addressed_request_checks_gate_before_relay_or_steward(branch, status):
    result, calls = run_route(branch, status)
    assert result.status == "succeeded", result.error
    assert calls[0] == ("GetRelayContext", {
        "resident_id": FACADE, "message_id": MESSAGE, "section": "request"})
    assert calls[1] == ("CheckRelayTarget", {"resident_id": FACADE, "member_id": MEMBER})
    assert result.outputs["target"] == MEMBER
    assert result.outputs["reason"] == REASONS[branch]
    assert len(calls) == 3
    if status in {"ready", "unknown"}:
        assert calls[2] == ("RelayMessage", {
            "resident_id": FACADE, "message_id": MESSAGE, "member_id": MEMBER})
        assert result.outputs["state"] == "relayed"
    else:
        name, args = calls[2]
        assert name == "DeliverMessage"
        assert args["resident_id"] == STEWARD
        assert args["business_ref"] == f"family-review/{FACADE}/message/{MESSAGE}"
        text = args["message"]["content_parts"][0]["text"]
        assert all(value in text for value in (FACADE, MEMBER, MESSAGE, REASONS[branch]))
        assert ("3" if status == "quota_low" else "没有可用账号") in text
        assert result.outputs["state"] == (
            "waiting_for_quota_review" if status == "quota_low" else "waiting_for_steward")
        assert run_route(branch, status)[1] == calls  # Same Event, same reminder identity.


@pytest.mark.parametrize("branch", REASONS)
def test_cancelled_addressed_request_does_not_relay_or_create_review(branch):
    result, calls = run_route(branch, "quota_low", cancelled=True)
    assert result.status == "succeeded", result.error
    assert result.outputs == {"state": "withdrawn"}
    assert [name for name, _ in calls] == ["GetRelayContext"]


@pytest.mark.parametrize("denied,status", [(True, "ready"), (False, "unexpected")])
def test_gate_failure_never_falls_through_to_delivery(denied, status):
    result, calls = run_route("direct_member_id", status, denied=denied)
    assert result.status == "failed"
    assert [name for name, _ in calls] == ["GetRelayContext", "CheckRelayTarget"]


def test_seed_router_upgrade_requires_the_complete_previous_candidate():
    old = (Path(__file__).parents[1] / "fixtures" / "d066_router_before_target_gate.txt").read_text()
    upgraded = family_definition.migrate_original_script(old)
    assert upgraded != old
    assert upgraded == family_definition.FAMILY_ROUTER_SCRIPT
    assert family_definition.migrate_original_script(upgraded) == upgraded
    custom = old.replace("按发送者的直连目标转交", "自定义转交条件")
    assert family_definition.migrate_original_script(custom) == custom

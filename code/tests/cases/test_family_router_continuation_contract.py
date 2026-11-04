"""Only explicit boolean continuation opts into the supplied previous round.

:module: lineage.team
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service import family_tasks
from tests.cases.test_family_router_script import MEMBERS, _call, _relay


@pytest.mark.parametrize("scope", ["in", "out"])
@pytest.mark.parametrize("value", [True, False, "missing"])
def test_relay_preserves_positive_continuation_in_both_admission_paths(scope, value):
    packet = {"target": MEMBERS[1], "confidence": 95, "scope": scope}
    if value != "missing":
        packet["continue_previous"] = value
    outcome, calls = _relay(packet)
    assert outcome.status == "succeeded"
    relayed = _call(calls, "RelayFamilyMessage")
    if value is True:
        assert relayed["continue_previous"] is True
    else:
        assert "continue_previous" not in relayed
    assert ("scope_gap" in relayed) == (scope == "out")


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, [], {}])
def test_invalid_packet_continuation_fails_before_delivery(value):
    outcome, calls = _relay({"target": MEMBERS[0], "confidence": 95, "continue_previous": value})
    assert outcome.status == "failed"
    assert [name for name, _ in calls] == ["DispatchFamilyTask"]
    assert "continue_previous" in calls[0][1]["facts"]["reason"]


def test_notice_cannot_opt_into_continuation():
    outcome, calls = _relay({"disposition": "notice", "continue_previous": True})
    assert outcome.status == "failed" and calls == []


@pytest.mark.parametrize("value", [True, False, None])
def test_isa_forwards_only_boolean_positive(monkeypatch, value):
    from management_portal.society_service import family_quota
    from management_portal.society_service.isa.conversation import relay_family_message
    received = []
    monkeypatch.setattr(family_quota, "relay_message", lambda *args, **kwargs: received.append(kwargs) or {})
    arguments = {"resident_id": "facade", "message_id": "message", "member_id": "member",
                 "continue_previous": value}
    relay_family_message(SimpleNamespace(argument=arguments.get, caller="router"))
    assert received[0]["continue_previous"] is (value is True)


@pytest.mark.parametrize("flag", ["notice", "reassign"])
@pytest.mark.parametrize("value", [True, False])
def test_isa_disallows_continuation_parameter_for_notice_or_reassign(flag, value):
    from management_portal.society_service.isa.conversation import relay_family_message
    arguments = {flag: True, "continue_previous": value}
    with pytest.raises(Exception, match="不能同时指定 continue_previous"):
        relay_family_message(SimpleNamespace(argument=arguments.get, caller="router"))


@pytest.mark.parametrize("value", ["", "true", 1, [], {}])
def test_isa_rejects_non_boolean_values(value):
    from management_portal.society_service.isa.conversation import relay_family_message
    with pytest.raises(Exception, match="continue_previous 必须为布尔值"):
        relay_family_message(SimpleNamespace(argument={"continue_previous": value}.get, caller="router"))


def test_old_default_migrates_but_custom_route_guide_and_prompt_survive():
    assert family_tasks.saved_fields("route", {"guide": family_tasks._PRE_CONTINUATION_ROUTE_GUIDE}) == {}
    custom = {"guide": "用户定制", "prompt": "自定义任务 ${facts}"}
    assert family_tasks.saved_fields("route", custom) == custom


def test_custom_and_already_running_router_receive_previous_round_contract(monkeypatch, tmp_path):
    from management_portal.society_service import resident_record_domain
    record = {"family": {"role": "router"}, "lineage_id": "lineage_test"}
    block = {"id": "fbi_test", "family": {"title": "团队", "facade": "resident_test"}}
    entry = {**family_tasks.defaults("route")["route"], "guide": "自定义路由指南",
             "prompt": "自定义任务 ${facts} ${guide_path} ${guide_version}"}
    monkeypatch.setattr(family_tasks, "context", lambda _: ({}, block))
    monkeypatch.setattr(family_tasks, "configured", lambda *_: {"route": entry})
    monkeypatch.setattr(resident_record_domain, "resident_owned_workspace_path", lambda _: tmp_path)
    guide = family_tasks.guide(record)[2]
    prompt = family_tasks.render(record, "route", {"previous_round": None})
    assert "自定义路由指南" in guide
    for text in (guide, prompt):
        assert family_tasks.ROUTER_CONTINUATION_CONTRACT in text
    assert "previous_round 为 null" in prompt
    assert "同一成员、时间相邻或话题相似都不是充分证据" in prompt

"""Reassigning a Family request to the member it should have gone to (decision_44cf587c1721).

The sender, a facade owner or the Family's steward names the right member;
the original member's waiting copy is withdrawn, the new member gets the
request from its original sender, the facade shows it with the new member,
and the correction is kept for the next routing and told to the steward.

:module: lineage.team
"""

from __future__ import annotations
from tests.cases.family_test_support import set_role

import pytest

from management_portal.society_service import (
    family_facade,
    family_reassign,
    lineage_graph_state,
    resident_conversation_stream,
    resident_directory_domain,
    resident_domain,
    resident_protocol,
)
from management_portal.society_service.resident_workspace_domain import _find_resident
from tests.cases.isa_test_support import run
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401
    _new_session_payload,
    future_creation_store,
)


def _register(title):
    record = resident_domain.register_resident(_new_session_payload(title), "sample_admin")
    resident_protocol.change_resident_state(record["resident_id"], "sample_admin", state_domain="intake", transition="close")
    return record["resident_id"]


def _stored(resident_id, message_id):
    return resident_conversation_stream.get_repository().message(resident_id, message_id)


def _state(resident_id, message_id):
    return resident_conversation_stream.get_repository().input_state(resident_id, message_id).get("state")


@pytest.fixture
def family(delivery_resident, monkeypatch):
    """A facade whose request from sample_admin went to the design member instead of the debug one."""

    wrong = delivery_resident["resident_id"]
    right = _register("family 设计")
    steward = _register("Family · steward")
    facade = _register("Family 门面")
    set_role(facade, family_id="fbi_reassign001", actor="sample_admin")
    members = {wrong, right}
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record: set(members))
    monkeypatch.setattr(family_facade, "family_steward_id", lambda record: steward)
    marks = {}
    monkeypatch.setattr(family_facade, "validate_relay", lambda business_ref, actor: marks.get(business_ref))
    told = []
    monkeypatch.setattr(family_facade, "tell_steward", lambda *args, **options: told.append((args, options)))

    def ask(text):
        request = run("DeliverMessage", {"resident_id": facade, "message": {
            "content_parts": [{"type": "text", "text": text}]}}, actor="sample_admin").raise_for_error()["message_id"]
        mark = family_facade.relay_mark(facade, request)
        marks[mark] = (facade, request)
        resident_protocol.deliver_resident_input({"resident_id": wrong, "message": {
            "message_id": f"msg_{request[-12:]}0000", "role": "user",
            "content_parts": _stored(facade, request)["content_parts"]}},
            "sample_admin", delivery_context=resident_protocol.ResidentDeliveryContext(business_ref=mark))
        return request

    return {"facade": facade, "wrong": wrong, "right": right, "steward": steward, "members": members,
            "ask": ask, "told": told}


def _reassign(family, request, member, actor="sample_admin", reason="这是 steward 设计，应交给 family 设计"):
    return run("RelayFamilyMessage", {"resident_id": family["facade"], "message_id": request, "member_id": member,
                                      "reassign": True, "reason": reason}, actor=actor)


def test_a_reassigned_request_moves_to_the_named_member_and_leaves_a_correction(family):
    """改派把门面请求交给指定成员，并留下改派纠正

    RelayFamilyMessage reassign=true 撤回原成员还在排队的副本，以原发送者把请求交给新成员；门面的转交记录指向新成员并保留改派来源；改派纠正写入 family_corrections_<门面>，并以 info 告诉 steward。
    """
    facade, wrong, right = family["facade"], family["wrong"], family["right"]
    request = family["ask"]("steward 的行动逻辑可以再优化")
    first_copy = family_facade.relay_links(_find_resident(facade))[request]["message_id"]
    assert _state(wrong, first_copy) == "queued"

    result = _reassign(family, request, right).raise_for_error()

    assert result["status"] == "reassigned" and result["member_id"] == right
    assert result["previous_member_id"] == wrong
    # The waiting copy is withdrawn; the new member has the request from its original sender.
    assert _state(wrong, first_copy) == "cancelled"
    copy = _stored(right, result["message_id"])
    assert copy["content_parts"] == [{"type": "text", "text": "steward 的行动逻辑可以再优化"}]
    assert copy["author_id"] == "sample_admin"
    link = family_facade.relay_links(_find_resident(facade))[request]
    assert link["resident_id"] == right and link["message_id"] == result["message_id"]
    assert [(item["resident_id"], item["state"]) for item in link["reassigned_from"]] == [(wrong, "queued")]
    # The next routing reads the correction; the steward hears of it.
    stored = lineage_graph_state.read_cell(_find_resident(facade)["lineage_id"], family_reassign.corrections_key(facade))
    [correction] = stored["value"]
    assert correction["request_message_id"] == request and correction["request"] == "steward 的行动逻辑可以再优化"
    assert correction["from"] == {"resident_id": wrong, "title": "Delivery receipt"}
    assert correction["to"] == {"resident_id": right, "title": "family 设计"}
    assert correction["judged_by"] == "sample_admin" and correction["reason"] == "这是 steward 设计，应交给 family 设计"
    [(args, options)] = family["told"]
    assert args[0] == facade and args[1] == family["steward"] and "改派" in args[3]
    assert options == {"activation_policy": "attach_to_next"}


def test_a_request_can_move_again_and_the_corrections_keep_the_latest(family, monkeypatch):
    """请求可以再次改派，改派纠正只保留最近的条目

    同一请求改派回原成员时投递新的副本、保留完整改派来源；改派纠正超过上限时去掉最早的条目。
    """
    facade, wrong, right = family["facade"], family["wrong"], family["right"]
    request = family["ask"]("登录页 500 了")
    _reassign(family, request, right).raise_for_error()
    back = _reassign(family, request, wrong, reason="还是登录问题").raise_for_error()

    link = family_facade.relay_links(_find_resident(facade))[request]
    assert link["resident_id"] == wrong and back["previous_member_id"] == right
    assert [item["resident_id"] for item in link["reassigned_from"]] == [wrong, right]
    # Each move delivers a copy of its own, so moving back is not taken as a retry of the first delivery.
    assert back["message_id"] != link["reassigned_from"][0]["message_id"]
    monkeypatch.setattr(family_reassign, "MAX_CORRECTIONS", 2)
    other = family["ask"]("权限页")
    _reassign(family, other, right).raise_for_error()
    stored = lineage_graph_state.read_cell(_find_resident(facade)["lineage_id"], family_reassign.corrections_key(facade))
    assert [item["request"] for item in stored["value"]] == ["登录页 500 了", "权限页"]


def test_a_reassignment_needs_a_relayed_request_another_member_and_no_turn_in_progress(family, monkeypatch):
    """改派需要已转交的请求、另一位成员且原成员没有在处理

    改派给当前成员、非成员或还没转交的请求按参数错误拒绝；原成员正在处理时要求先停止本轮；请求发送者、门面 owner 和本 Family 的 steward 以外的人无权改派。
    """
    facade, wrong, right = family["facade"], family["wrong"], family["right"]
    request = family["ask"]("登录页 500 了")
    copy = family_facade.relay_links(_find_resident(facade))[request]["message_id"]

    assert _reassign(family, request, wrong).error["code"] == "instruction.invalid_arguments"
    assert _reassign(family, request, "resident_ffffffffffff").error["code"] == "instruction.invalid_arguments"
    waiting = run("DeliverMessage", {"resident_id": facade, "message": {
        "content_parts": [{"type": "text", "text": "还没转交"}]}}, actor="sample_admin").raise_for_error()["message_id"]
    assert _reassign(family, waiting, right).error["code"] == "instruction.invalid_arguments"
    resident_conversation_stream.get_repository().set_input_state(wrong, copy, "running")
    refused = _reassign(family, request, right)
    assert refused.error["code"] == "instruction.invalid_arguments" and "先停止本轮" in refused.error["message"]
    assert family_facade.relay_links(_find_resident(facade))[request]["resident_id"] == wrong
    # Someone else's request is not theirs to move unless they own the facade or are its steward.
    monkeypatch.setattr(resident_directory_domain, "_can_manage", lambda record, actor: False)
    assert _reassign(family, request, right, actor="CASE-A").error["code"] == "instruction.forbidden"
    assert family["told"] == []

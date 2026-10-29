"""A Resident's next request to a facade goes to the member that took its latest one, without the Router.

:module: lineage.family
:design: decision_44cf587c1721
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from management_portal.hatg.contracts import InstructionContext
from management_portal.society_service import family_facade, lineage_message_events, resident_conversation_stream
from management_portal.society_service.lineage_program_effect_execution import _family_note_context
from management_portal.society_service.resident_input_queue_domain import build_message as _message
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.test_family_quota import quota_family, team, owners  # noqa: F401

SENDER = "resident_5e4de4000001"
OTHER_SENDER = "resident_5e4de4000002"
PERSON = "storage-outsider"
EFFECT = "effect_123456789abc"


def _ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def family(quota_family):
    f = quota_family
    repo = resident_conversation_stream.get_repository()
    record = _find_resident(f.facade)

    def send(author, *, minutes_ago=0.0, quote=None, parts=None):
        message = _message("user", "下一步", author, created_at=_ago(minutes_ago), content_parts=parts)
        if quote:
            message["quote"] = quote
        repo.append_message(f.facade, message)
        repo.set_input_state(f.facade, message["message_id"], "queued")
        return repo.message(f.facade, message["message_id"])

    def relayed(author, member_id, *, minutes_ago):
        """An earlier request of ``author`` that the Family relayed to ``member_id``."""
        source = send(author, minutes_ago=minutes_ago + 1)
        with get_lineage_data_store().family_relays.transaction(record["lineage_id"], f.facade) as state:
            state.setdefault("links", {})[source["message_id"]] = {
                "resident_id": member_id, "message_id": "msg_copy" + source["message_id"][-16:],
                "relayed_at": _ago(minutes_ago)}
        repo.set_input_state(f.facade, source["message_id"], "completed")
        return source

    return SimpleNamespace(f=f, repo=repo, send=send, relayed=relayed, record=lambda: _find_resident(f.facade))


@pytest.mark.parametrize("case", ["continues", "person", "quote", "stale", "steward", "left", "other-sender",
                                  "member-sender", "relayed-after", "no-history"])
def test_a_residents_next_request_continues_with_the_member_of_its_latest_one(family, case):
    """Resident 接着发给门面的请求交给承接它上一条请求的成员

    发送者是 Family 以外的 Resident、请求不带引用、它最近一条已转交的请求在 4 小时内交给了仍在名册、能读这条请求附件的成员时，直接交给该成员；
    人发的请求、带引用的请求、上一条交给 steward、超过 4 小时、成员已离开、来自别的发送者或在这条请求到达之后才转交的，都由 Router 选择。

    :importance: P1
    :design: decision_44cf587c1721
    """
    f, author, expected = family.f, SENDER, family.f.member
    if case == "person":
        family.relayed(PERSON, f.member, minutes_ago=10)
        author, expected = PERSON, ""
    elif case == "stale":
        family.relayed(SENDER, f.member, minutes_ago=5 * 60)
        expected = ""
    elif case == "steward":
        # The latest request decides: organization work does not make the next one the steward's.
        family.relayed(SENDER, f.member, minutes_ago=20)
        family.relayed(SENDER, f.steward, minutes_ago=10)
        expected = ""
    elif case == "left":
        family.relayed(SENDER, f.spare, minutes_ago=10)
        expected = ""
    elif case == "other-sender":
        family.relayed(OTHER_SENDER, f.member, minutes_ago=10)
        expected = ""
    elif case == "member-sender":
        family.relayed(f.member, f.member, minutes_ago=10)
        author, expected = f.member, ""
    elif case == "no-history":
        expected = ""
    else:
        family.relayed(SENDER, f.member, minutes_ago=10)
    if case == "relayed-after":
        message = family.send(SENDER, minutes_ago=30)
        expected = ""
    elif case == "quote":
        message = family.send(SENDER, quote={"message_id": f.message, "author_id": SENDER, "text": "持续检查权限"})
        expected = ""
    else:
        message = family.send(author)
    assert family_facade.continued_member(family.record(), message) == expected


def test_the_arrival_names_the_continued_member_and_the_facade_shows_it_delivering(family):
    """到达事件带上延续成员，门面显示正在交给该成员

    :importance: P2
    :design: decision_44cf587c1721
    """
    f = family.f
    family.relayed(SENDER, f.member, minutes_ago=10)
    message = family.send(SENDER)
    details = lineage_message_events.arrival_details(family.repo, f.facade, message["message_id"], SENDER, family.record())
    assert details["quoted_member_id"] == "" and details["continued_member_id"] == f.member
    projected = family_facade.project_messages(family.record(), [dict(message)],
                                               input_states={message["message_id"]: "queued"})
    assert projected[0]["relay"]["state"] == "delivering" and projected[0]["relay"]["resident_id"] == f.member


@pytest.mark.parametrize("change", ["none", "forged-member", "no-payload", "no-history", "other-author"])
def test_the_router_effect_may_relay_only_the_member_it_recomputes(family, change):
    """Router Effect 只能把延续请求交给按已存转交重新算出的成员

    转交授权不信任事件载荷：成员、载荷和按已存转交重新计算的延续成员三者一致才以门面身份转交；对延续请求询问 Router 仍然允许。

    :importance: P1
    :design: decision_44cf587c1721
    """
    f = family.f
    if change != "no-history":
        family.relayed(SENDER, f.member, minutes_ago=10)
    message = family.send(SENDER)
    record = family.record()
    view = {"function_blocks": [{"id": record["family"]["family_id"],
             "elements": [{"id": EFFECT, "kind": "component", "key": "router"}],
             "family": {"facade": f.facade, "members": [f.member], "steward": f.steward}}]}
    args = dict(resident_id=f.facade, message_id=message["message_id"], member_id=f.member)
    payload = {**args, "quoted_member_id": "", "continued_member_id": f.member}
    caller = SENDER
    if change == "forged-member":
        args["member_id"] = payload["continued_member_id"] = f.spare
    elif change == "no-payload":
        payload["continued_member_id"] = ""
    elif change == "other-author":
        caller = OTHER_SENDER
    event = SimpleNamespace(type="resident.message_received", subject_resident_id=f.facade, payload=payload)

    def acting_as(instruction, arguments):
        return _family_note_context(view, EFFECT, instruction, arguments,
                                    InstructionContext(caller=caller, entry="lineage"),
                                    lineage_id=record["lineage_id"], event=event).caller

    assert (acting_as("RelayFamilyMessage", args) == f.facade) == (change == "none")
    asked = acting_as("DispatchFamilyTask", dict(resident_id=f.facade, source_message_id=message["message_id"], trigger="route"))
    assert (asked == f.facade) == (change != "other-author")

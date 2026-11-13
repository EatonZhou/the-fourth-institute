"""Recommendation choices preserve source, sender and one delivery, without expanding consent."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_common.resident_handoff_cards import cards, projection, selection_id, selection_part, prompt_context
from management_common.principal import message_origin
from management_common.async_questions import visible_message_input_ids
from management_portal.society_service import (
    resident_handoff_selection, resident_conversation_stream, resident_protocol,
    resident_intake_domain, resident_input_queue_domain, family_facade, family_quota,
    lineage_message_events, resident_prompt_domain, family_chat_preferences,
)
from tests.cases.test_family_chat_preferences import family_chat, lineage, charters, future_creation_store  # noqa: F401
from tests.cases.isa_test_support import ACTOR

REAL_DELIVER = resident_protocol.deliver_resident_input
REAL_INTAKE = resident_intake_domain.deliver_resident_message_content
CARD = {"resident_id": "resident_222222222222", "title": "Family 实现", "reason": "负责个人助手", "ask_message": "未展示的建议草稿"}


def source():
    return {"message_id": "msg_" + "b" * 32, "role": "agent", "content_parts": [
        {"type": "text", "text": "选择协作者\n```portal-resident-handoff\n" + json.dumps([CARD, {**CARD, "title": "另一个选项"}]) + "\n```"}]}


def choice(original, index=0):
    return {"message_id": selection_id(original["message_id"], index), "role": "user",
            "quote": {"message_id": original["message_id"]}, "content_parts": [
                {"type": "text", "text": "替我问一下"}, selection_part(original["message_id"], index, cards(original)[index])]}


def test_source_and_choice_contract(monkeypatch):
    original = source()
    assert len(cards(original)) == 2
    assert selection_id(original["message_id"], 0) != selection_id(original["message_id"], 1)
    assert projection(original)[0]["selection_message_id"] in visible_message_input_ids([original])
    literal = deepcopy(original)
    literal["content_parts"][0]["text"] = "````markdown\n" + original["content_parts"][0]["text"] + "\n````"
    assert cards(literal) == []
    record = {"resident_id": "resident_111111111111"}
    monkeypatch.setattr(family_facade, "is_facade", lambda _: False)
    repo = SimpleNamespace(message=lambda resident, mid: original if mid == original["message_id"] else None)
    selected = choice(original, 1)
    resident_handoff_selection.prepare(record, selected, repo)
    assert "未展示的建议草稿" not in prompt_context(selected)
    assert "另一个选项" in resident_prompt_domain._build_plain_user_prompt({"message": "替我问一下", "input_message": selected})
    for mutation in ("target", "source", "identity", "quote", "draft"):
        invalid = deepcopy(selected)
        if mutation == "target": invalid["content_parts"][1]["resident_id"] = "resident_ffffffffffff"
        if mutation == "source": invalid["content_parts"][1]["source_message_id"] = "msg_missing"
        if mutation == "identity": invalid["message_id"] = "msg_" + "c" * 32
        if mutation == "quote": invalid["quote"] = {"message_id": "msg_unrelated"}
        if mutation == "draft": invalid["content_parts"][1]["ask_message"] = "extra instructions"
        with pytest.raises(ValueError):
            resident_handoff_selection.prepare(record, invalid, repo)


def test_family_choice_delivers_to_writer_once_without_router(family_chat, monkeypatch):
    monkeypatch.setattr(resident_protocol, "deliver_resident_input", REAL_DELIVER)
    monkeypatch.setattr(resident_intake_domain, "deliver_resident_message_content", REAL_INTAKE)
    monkeypatch.setattr(family_quota.execution_port, "service", lambda: Mock(accounts=lambda: {"accounts": []}))
    facade, member = family_chat
    repository = resident_conversation_stream.get_repository()
    original = resident_input_queue_domain.build_message("agent", source()["content_parts"][0]["text"], facade["resident_id"])
    original.update(recipients=[{"kind": "user", "id": ACTOR}], origin=message_origin(facade["resident_id"], initiated_by=member))
    repository.append_message(facade["resident_id"], original)
    selected = choice(original)
    # The arrival event is not needed to route this input.
    monkeypatch.setattr(lineage_message_events, "wake_watchers", lambda *a: True)
    payload = {"resident_id": facade["resident_id"], "message": selected}
    accepted = REAL_DELIVER(payload, ACTOR)
    stored = repository.message(facade["resident_id"], accepted["message_id"])
    link = family_facade.relay_links(facade)[accepted["message_id"]]
    assert link["resident_id"] == member
    delivered = repository.message(member, link["message_id"])
    assert delivered["author_id"] == ACTOR
    assert delivered["content_parts"] == selected["content_parts"]
    assert delivered["content_parts"][0]["text"] == "替我问一下"
    assert REAL_DELIVER(payload, ACTOR)["message_id"] == accepted["message_id"]
    assert len(repository.input_states(member)) == 1
    # An explicit card choice retains its writer even after Solo preference changes.
    monkeypatch.setattr(family_facade, "routing_snapshot", lambda *a: {"solo_member_id": "resident_ffffffffffff", "muted_member_ids": []})
    assert family_facade.direct_member(facade, stored) == member
    family_chat_preferences.validate_target(facade, stored, member)
    monkeypatch.setattr(family_facade, "quoted_member", lambda *a: "")
    with pytest.raises(ValueError, match="未重新路由"):
        family_facade.direct_member(facade, stored)
    with pytest.raises(ValueError, match="未重新路由"):
        resident_handoff_selection.prepare(facade, stored, repository)

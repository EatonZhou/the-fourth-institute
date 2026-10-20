"""Typed answers follow accepted nested Family relays without copying questions.

:module: lineage.hatg
"""
from copy import deepcopy

import pytest

from management_common.async_questions import question_answer_id, question_message_parts
from management_common.principal import message_origin
from management_portal.society_service import family_facade, family_quota, resident_conversation_stream
from management_portal.society_service.resident_intake_domain import _message
from management_portal.society_service.resident_questions import prepare_question_answer
from management_portal.society_service.resident_workspace_domain import _find_resident
from tests.cases.family_test_support import set_role
from tests.cases.isa_test_support import run
from tests.cases.test_cross_owner_storage_integration import ACTOR
from tests.cases.test_family_quota import quota_family, owners, team  # noqa: F401


@pytest.fixture
def nested_answer(quota_family, monkeypatch):
    f = quota_family
    set_role(f.member, role="facade", family_id="fbi_inner", actor=ACTOR)
    set_role(f.spare, role="member", family_id="fbi_inner", actor=ACTOR)
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record:
                        {f.member} if record["resident_id"] == f.facade else {f.spare})
    repo = resident_conversation_stream.get_repository()
    question = _message("agent", "Choose scope", f.facade)
    question.update(
        content_parts=question_message_parts("Choose scope", [{"question_index": 0, "title": "Which scope?"}],
                                             f.facade, question["message_id"]),
        recipients=[{"kind": "user", "id": ACTOR}],
        origin=message_origin(f.facade, initiated_by=f.spare),
    )
    repo.append_message(f.facade, question)
    answer_id = question_answer_id(f.facade, question["message_id"])
    answer = {"message_id": answer_id, "content_parts": [
        {"type": "text", "text": "Only the requested scope"},
        {"type": "async_question_answer", "source_message_id": question["message_id"],
         "answers": [{"question_index": 0, "answer": "Only the requested scope"}]},
    ]}
    run("DeliverMessage", {"resident_id": f.facade, "message": answer}, actor=ACTOR,
        key="nested-answer").raise_for_error()
    first = family_quota.relay_message(f.facade, answer_id, f.member, ACTOR)
    assert first["message_id"] == answer_id
    assert family_facade.relay_source_of(f.member, answer_id) == (f.facade, answer_id)
    assert repo.message(f.member, question["message_id"]) is None
    return f, repo, question, answer_id


def test_nested_answer_reaches_writer_once_with_original_question_and_author(nested_answer):
    f, repo, question, answer_id = nested_answer
    original = repo.message(f.facade, answer_id)
    before = deepcopy(original)
    inner = repo.message(f.member, answer_id)
    assert "message_id" not in inner["quote"]
    assert family_facade.quoted_member(_find_resident(f.member), inner) == f.spare
    result = family_quota.relay_message(f.member, answer_id, f.spare, ACTOR)
    assert result["status"] == "relayed"
    delivered = repo.message(f.spare, answer_id)
    assert delivered["content_parts"] == original["content_parts"]
    assert delivered["author_id"] == original["author_id"] == ACTOR
    assert delivered["quote"] == inner["quote"]
    assert repo.message(f.spare, question["message_id"]) is None
    assert repo.question_set_state(f.facade, question["message_id"]) == "answered"
    assert family_facade.relay_sources_of(f.spare, answer_id) == [(f.member, answer_id), (f.facade, answer_id)]
    cursor = repo.current_projection(f.spare)["cursor"]
    assert family_quota.relay_message(f.member, answer_id, f.spare, ACTOR) == result
    assert repo.current_projection(f.spare)["cursor"] == cursor
    assert repo.message(f.facade, answer_id) == before


@pytest.mark.parametrize("mutation", ["unlinked", "changed-author", "changed-ancestor", "changed-answer",
                                     "wrong-target", "wrong-source", "closed", "missing-question"])
def test_nested_answer_requires_accepted_unchanged_ancestry_and_live_question(nested_answer, monkeypatch, mutation):
    f, repo, question, answer_id = nested_answer
    candidate = deepcopy(repo.message(f.member, answer_id))
    target = f.spare
    real_message = repo.message
    ancestor = deepcopy(repo.message(f.facade, answer_id))
    if mutation == "unlinked":
        monkeypatch.setattr(family_facade, "relay_links", lambda *_: {})
    elif mutation == "changed-author":
        ancestor["author_id"] = "another-user"
    elif mutation == "changed-ancestor":
        ancestor["content_parts"][1]["answers"][0]["answer"] = "different"
    elif mutation == "changed-answer":
        candidate["content_parts"][1]["answers"][0]["answer"] = "different"
    elif mutation == "wrong-target":
        target = f.steward
    elif mutation == "wrong-source":
        candidate["content_parts"][1]["source_message_id"] = "msg_unrelated"
    elif mutation == "closed":
        monkeypatch.setattr(repo, "question_set_state", lambda *_: "closed")
    def lookup(holder, mid):
        if (holder, mid) == (f.facade, answer_id):
            return ancestor
        if mutation == "missing-question" and (holder, mid) == (f.facade, question["message_id"]):
            return None
        return real_message(holder, mid)
    monkeypatch.setattr(repo, "message", lookup)
    with pytest.raises(ValueError, match="问题不存在|问题已关闭"):
        prepare_question_answer(target, candidate, repo, relay_source=(f.member, answer_id))
    assert real_message(f.spare, answer_id) is None


def test_cancelled_nested_answer_is_not_delivered(nested_answer):
    f, repo, _, answer_id = nested_answer
    repo.set_input_state(f.member, answer_id, "cancelled")
    assert family_quota.relay_message(f.member, answer_id, f.spare, ACTOR)["status"] == "withdrawn"
    assert repo.message(f.spare, answer_id) is None

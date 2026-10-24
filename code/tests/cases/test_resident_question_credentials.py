"""A question set can ask the user to lend permissions; the answer lends exactly those.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10, decision_b820faf42218
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from management_common.async_questions import (
    ANSWER_PART, CREDENTIAL_APPROVE, CREDENTIAL_DECLINE, approved_credentials, decode_question_content,
)
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.isa import conversation

RESIDENT = "resident_aaaaaaaaaaaa"
LEND = {"a": "@contributor", "resource_id": "workbench_0123456789ab"}


def fence(*questions):
    return "需要你的确认。\n```portal-question\n" + json.dumps(list(questions), ensure_ascii=False) + "\n```\n"


def questions_of(text):
    parts = decode_question_content([{"type": "text", "text": text}], RESIDENT, "msg_ask")
    return next(part for part in parts if part["type"] == "async_questions")["questions"]


def test_permission_request_has_fixed_options_and_exact_permissions():
    """权限请求问题的选项固定为同意与不交出，并保留去重后的精确权限"""
    questions = questions_of(fence(
        {"title": "交出协作权限来发布吗？", "options": ["随便"], "credentials": [LEND, dict(LEND)]},
        {"title": "范围？", "options": ["定向"]},
    ))
    assert questions[0]["options"] == [CREDENTIAL_APPROVE, CREDENTIAL_DECLINE]
    assert questions[0]["credentials"] == [LEND]
    assert "credentials" not in questions[1] and questions[1]["options"] == ["定向"]


@pytest.mark.parametrize("credentials", [
    [], [{"a": "@contributor"}], [{"a": "@contributor", "resource_id": ""}],
    [{"a": "@contributor", "resource_id": "workbench x"}], [{**LEND, "expires_in_seconds": 60}], "@contributor",
    [LEND] * 21,
])
def test_malformed_permission_request_stays_text(credentials):
    """权限请求格式不精确或超过一条消息可交出的数量时整段保持为文本"""
    parts = [{"type": "text", "text": fence({"title": "交出权限？", "credentials": credentials})}]
    assert decode_question_content(parts, RESIDENT, "msg_ask") == parts


def test_only_approved_requests_lend_their_permissions():
    """只有回答为同意的权限请求问题交出其权限"""
    other = {"a": "UpdateResource", "resource_id": "repo_0123456789ab"}
    questions = questions_of(fence(
        {"title": "交出工作台权限？", "credentials": [LEND]},
        {"title": "交出仓库权限？", "credentials": [other, LEND]},
        {"title": "范围？"},
    ))
    answers = [{"question_index": 0, "answer": CREDENTIAL_DECLINE},
               {"question_index": 1, "answer": " " + CREDENTIAL_APPROVE + " "},
               {"question_index": 2, "answer": CREDENTIAL_APPROVE}]
    assert approved_credentials(questions, answers) == [other, LEND]
    assert approved_credentials(questions, [{"question_index": 0, "answer": CREDENTIAL_DECLINE}]) == []


def answer(*entries):
    return {"role": "user", "content_parts": [
        {"type": "text", "text": "回答"},
        {"type": ANSWER_PART, "source_message_id": "msg_ask", "answers": list(entries)},
    ]}


@pytest.fixture
def asked(monkeypatch):
    text = fence({"title": "交出协作权限？", "credentials": [LEND]}, {"title": "范围？"})
    source = {"message_id": "msg_ask", "role": "agent",
              "content_parts": decode_question_content([{"type": "text", "text": text}], RESIDENT, "msg_ask")}
    repository = SimpleNamespace(message=lambda resident_id, message_id: (
        source if (resident_id, message_id) == (RESIDENT, "msg_ask") else None))
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)


def test_answer_lends_exactly_what_it_approves(asked):
    """回答随消息交出的权限必须正好是所同意问题请求的权限"""
    approve = answer({"question_index": 0, "answer": CREDENTIAL_APPROVE}, {"question_index": 1, "answer": "定向"})
    decline = answer({"question_index": 0, "answer": CREDENTIAL_DECLINE}, {"question_index": 1, "answer": "定向"})
    conversation._check_answer_credentials(RESIDENT, approve, [LEND])
    conversation._check_answer_credentials(RESIDENT, decline, [])
    # An ordinary message keeps lending whatever its sender chooses.
    conversation._check_answer_credentials(RESIDENT, {"role": "user", "content_parts": [{"type": "text", "text": "hi"}]}, [LEND])
    extra = {"a": "@admin", "resource_id": "workbench_0123456789ab"}
    for message, lent in [(approve, []), (approve, [LEND, extra]), (approve, [LEND, LEND]), (decline, [LEND])]:
        with pytest.raises(InstructionError) as error:
            conversation._check_answer_credentials(RESIDENT, message, lent)
        assert error.value.code == "instruction.invalid_arguments"
    # A set this Resident did not ask (a facade's member reply) lends nothing.
    with pytest.raises(InstructionError):
        conversation._check_answer_credentials("resident_bbbbbbbbbbbb", approve, [LEND])

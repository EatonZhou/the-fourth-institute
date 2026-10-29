from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from management_common.message_identity import derived_message_identity


QUESTION_PART = "async_questions"
ANSWER_PART = "async_question_answer"
# The error code on a set's answer slot when a person closed the set unanswered.
QUESTION_SET_CLOSED = "question_set_closed"
QUESTION_CONTENT_INSTRUCTIONS = """需要用户做选择或补充信息时，在回复末尾输出提问卡片：使用一个独立的 portal-question 代码围栏，
围栏内填写一个问题对象，或多个问题对象组成的数组；每个问题必填 title（问题文字），可选 options（字符串数组）。例如：
```portal-question
[{"title":"选择检查范围？","options":["定向检查","全部检查"]},{"title":"是否同时更新规范？","options":["更新","暂不更新"]}]
```
同一条回复中的全部问题组成一组。Portal 在这条回复中逐题展示，用户答完整组后一次提交，答案作为一条 user 消息送达。
需要用户把其持有的权限交给你时（你的身份在某资源上缺少权限），在问题对象中加 credentials，逐项写出 A 树节点 a 与资源 resource_id，title 说明用途，例如
{"title":"同意把这个工作台的协作权限交给我来发布吗？","credentials":[{"a":"@contributor","resource_id":"workbench_0123456789ab"}]}。
这类问题的选项固定为“同意交出这些权限”和“不交出”。用户同意时凭证随这条回答签发，只在你处理这条回答期间有效，在这一轮内用完；不要让用户另行签发。
提问后结束本轮，等待回答，不要继续做依赖这些答案的工作。用户可能先发其他消息；每组问题在回答前一直有效，回答到达时再按回答继续。
只在真正需要用户回答时使用；展示语法示例时使用外层代码围栏。无需调用接口或工具写消息。"""
# A question with credentials asks the user to lend those permissions with the
# answer; its options are fixed and the first one approves (decision_1e2eb869bc10).
CREDENTIAL_APPROVE = "同意交出这些权限"
CREDENTIAL_DECLINE = "不交出"
# DeliverMessage lends at most this many credentials with one message.
MAX_LENT_CREDENTIALS = 20


def _credentials(value: object) -> list[dict[str, str]] | None:
    """The ``{a, resource_id}`` permissions a question asks to be lent; None when malformed."""

    if not isinstance(value, list) or not value or len(value) > MAX_LENT_CREDENTIALS:
        return None
    found: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"a", "resource_id"}:
            return None
        if not all(isinstance(item[key], str) for key in ("a", "resource_id")):
            return None
        entry = {"a": item["a"].strip(), "resource_id": item["resource_id"].strip()}
        if (not entry["a"] or not entry["resource_id"] or len(entry["a"]) > 120 or len(entry["resource_id"]) > 240
                or any(character.isspace() for character in entry["a"] + entry["resource_id"])):
            return None
        if entry not in found:
            found.append(entry)
    return found


def _fenced_questions(body: str) -> list[dict[str, Any]] | None:
    """The questions one ``portal-question`` fence holds: one object or a list of them."""

    if len(body) > 65536:
        return None
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return None
    values = value if isinstance(value, list) else [value]
    if not values or not all(
        isinstance(item, dict)
        and set(item) <= {"title", "options", "credentials"}
        and isinstance(item.get("title"), str)
        and item["title"].strip()
        and isinstance(item.get("options", []), list)
        and all(isinstance(option, str) and option.strip() for option in item.get("options", []))
        and ("credentials" not in item or _credentials(item["credentials"]) is not None)
        for item in values
    ):
        return None
    return normalize_async_questions(values)


def decode_question_content(
    parts: list[dict[str, Any]], resident_id: str, message_id: str,
) -> list[dict[str, Any]]:
    """Turn the ``portal-question`` fences of an Agent message into its one question set.

    Every question the message asks belongs to the same set; the set's part
    takes the place of the first fence, and the text around the fences stays.
    """

    if any(part.get("type") == QUESTION_PART for part in parts):
        return parts
    decoded: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    set_position = -1
    for part in parts:
        if part.get("type") != "text":
            decoded.append(part)
            continue
        lines = str(part.get("text") or "").splitlines(keepends=True)
        text: list[str] = []
        position = 0
        while position < len(lines):
            opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)\r?\n?", lines[position])
            if not opening:
                text.append(lines[position])
                position += 1
                continue
            fence, info = opening.groups()
            end = position + 1
            closing = re.compile(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*\r?\n?")
            while end < len(lines) and not closing.fullmatch(lines[end]):
                end += 1
            fenced = (
                _fenced_questions("".join(lines[position + 1:end]))
                if info.strip() == "portal-question" and end < len(lines) else None
            )
            if fenced is None:
                text.extend(lines[position:end + 1])
            else:
                if text:
                    decoded.append({**part, "text": "".join(text)})
                    text = []
                if set_position < 0:
                    set_position = len(decoded)
                questions.extend(fenced)
            position = end + 1
        if text:
            decoded.append({**part, "text": "".join(text)})
    if not questions:
        return parts
    for index, question in enumerate(questions):
        question["question_index"] = index
    decoded[set_position:set_position] = question_message_parts("", questions, resident_id, message_id)
    return decoded


def question_set(message: dict[str, Any] | None) -> dict[str, Any] | None:
    """The question set an Agent message asks; None when it asks none."""

    if not message or message.get("role") != "agent":
        return None
    return next((
        part for part in message.get("content_parts") or []
        if isinstance(part, dict) and part.get("type") == QUESTION_PART
    ), None)


def answered_set(message: Mapping[str, Any] | None) -> str:
    """The message whose question set a user message answers; "" when it answers none."""

    for part in (message or {}).get("content_parts") or []:
        if isinstance(part, Mapping) and part.get("type") == ANSWER_PART:
            return str(part.get("source_message_id") or "").strip()
    return ""


def visible_message_input_ids(messages: list[dict[str, Any]]) -> list[str]:
    identities = {message["message_id"] for message in messages}
    identities.update(
        str(questions["answer_message_id"])
        for questions in map(question_set, messages) if questions
    )
    return sorted(identities)


def normalize_async_questions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    questions = []
    for index, question in enumerate(value):
        if not isinstance(question, dict):
            continue
        title = question.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        options = question.get("options")
        normalized = {
            "question_index": index,
            "title": title.strip(),
            "options": [
                option for option in options
                if isinstance(option, str) and option.strip()
            ] if isinstance(options, list) else [],
        }
        if "credentials" in question:
            credentials = _credentials(question["credentials"])
            if credentials is None:
                continue  # Never show a permission request without its exact permissions.
            normalized.update(options=[CREDENTIAL_APPROVE, CREDENTIAL_DECLINE], credentials=credentials)
        questions.append(normalized)
    return questions


def approved_credentials(questions: list[dict[str, Any]], answers: object) -> list[dict[str, str]]:
    """The permissions an answer lends: those of the credential questions it approves."""

    chosen = {
        entry.get("question_index"): entry["answer"].strip()
        for entry in answers if isinstance(entry, dict) and isinstance(entry.get("answer"), str)
    } if isinstance(answers, list) else {}
    lent: list[dict[str, str]] = []
    for question in questions:
        if question.get("credentials") and chosen.get(question.get("question_index")) == CREDENTIAL_APPROVE:
            lent.extend(item for item in question["credentials"] if item not in lent)
    return lent


def question_answer_id(resident_id: str, message_id: str) -> str:
    """The one answer message a question set can receive."""

    return derived_message_identity("resident-question-answer", resident_id, message_id)


def question_message_parts(
    text: str, questions: list[dict[str, Any]], resident_id: str, message_id: str,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    if questions:
        parts.append({
            "type": QUESTION_PART,
            "answer_message_id": question_answer_id(resident_id, message_id),
            "questions": [dict(question) for question in questions],
        })
    return parts

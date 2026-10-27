from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from management_common.async_questions import ANSWER_PART, question_set


def answered_set(message: Mapping[str, Any] | None) -> str:
    """The message whose question set a user message answers; "" when it answers none."""

    for part in (message or {}).get("content_parts") or []:
        if isinstance(part, Mapping) and part.get("type") == ANSWER_PART:
            return str(part.get("source_message_id") or "").strip()
    return ""


def prepare_question_answer(
    resident_id: str,
    message: Mapping[str, Any] | None,
    repository: Any,
    *,
    facade: Mapping[str, Any] | None = None,
    relay_source: tuple[str, str] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Check one answer to a question set and build the user message that carries it.

    A set takes one answer, with an answer for every question; it stays open
    until then, however the Conversation goes on, unless a person closes it.
    On a Family facade a member's reply shown there may ask the questions: the
    set and its state are the member's, and the answer goes back to it with
    the set's own answer message ID.
    """

    parts = (message or {}).get("content_parts") or []
    answers = [part for part in parts if part.get("type") == ANSWER_PART]
    if not answers:
        return None
    if len(answers) != 1 or any(part.get("type") not in {"text", ANSWER_PART} for part in parts):
        raise ValueError("问题回答必须包含一个回答和文本")
    source_id = str(answers[0].get("source_message_id") or "").strip()
    entries = answers[0].get("answers")
    holder = resident_id
    source = repository.message(resident_id, source_id)
    relayed_answer = False
    if source is None and relay_source is not None:
        # Outward questions live at the sending facade, which may be above
        # the immediate relay. Only follow accepted persisted links carrying
        # this same answer; never search arbitrary conversations by question ID.
        from . import family_facade
        from . import resident_workspace_domain

        relay_facade = resident_workspace_domain.find_resident(relay_source[0])
        original = repository.message(*relay_source)
        if (relay_facade is not None
                and answered_set(original) == source_id
                and original.get("message_id") == message.get("message_id")
                and original.get("content_parts") == parts):
            for ancestor in [relay_source, *family_facade.relay_sources_of(*relay_source)]:
                accepted = repository.message(*ancestor) or {}
                if (accepted.get("message_id") != message.get("message_id")
                        or accepted.get("author_id") != original.get("author_id")
                        or accepted.get("content_parts") != parts):
                    break
                question = repository.message(ancestor[0], source_id)
                if question is not None:
                    # Resolve the original writer against the current relay's
                    # Family, not the ancestor's roster or a quote excerpt.
                    if family_facade._outward_quote_owner(relay_facade, question) == resident_id:
                        holder, source = ancestor[0], question
                        relayed_answer = True
                    break
    if source is None and facade is not None:
        from .family_facade import relayed_reply

        found = relayed_reply(facade, source_id)
        holder, source = (str(found["worker_id"]), found["reply"]) if found else ("", None)
    questions = question_set(source)
    if questions is None:
        raise ValueError("该 Resident 中的问题不存在或已不可用")
    if message.get("message_id") != questions["answer_message_id"]:
        raise ValueError("问题回答的 message_id 与问题不匹配")
    titles = {question["question_index"]: question["title"] for question in questions["questions"]}
    if not isinstance(entries, list) or any(
        not isinstance(entry, Mapping)
        or type(entry.get("question_index")) is not int
        or not isinstance(entry.get("answer"), str)
        or not entry["answer"].strip()
        for entry in entries
    ):
        raise ValueError("每个问题都需要一个回答")
    indices = [entry["question_index"] for entry in entries]
    if sorted(indices) != sorted(titles) or len(set(indices)) != len(indices):
        raise ValueError("请回答这组的全部问题，再一起提交")
    state = repository.question_set_state(holder, source_id)
    if state == "closed":
        raise ValueError("这组问题已关闭，不再回答")
    if state == "answered" and holder != resident_id and not relayed_answer and repository.message(resident_id, message["message_id"]) is None:
        raise ValueError("这组问题已经在成员的对话中回答过了")
    answered = [
        {"question_index": index, "answer": entry["answer"].strip()}
        for index in sorted(titles)
        for entry in entries if entry["question_index"] == index
    ]
    if len(answered) == 1:
        content = f"关于你提出的问题「{titles[answered[0]['question_index']]}」，我的回答是：\n{answered[0]['answer']}"
    else:
        content = "关于你提出的问题，我的回答是：\n" + "\n".join(
            f"{number}. 「{titles[entry['question_index']]}」：{entry['answer']}"
            for number, entry in enumerate(answered, 1)
        )
    return content, {
        "message_id": questions["answer_message_id"],
        "role": "user",
        "content_parts": [
            {"type": "text", "text": content},
            {"type": ANSWER_PART, "source_message_id": source_id, "answers": answered},
        ],
    }

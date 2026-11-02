"""Bind a person's plain consent to an already recorded, precise permission question."""
from __future__ import annotations

from management_common.async_questions import ANSWER_PART, CREDENTIAL_APPROVE, approved_credentials, question_set
from management_common.principal import actor_principal


APPROVALS = frozenset({CREDENTIAL_APPROVE, "我给你临时凭证", "同意授予这些临时权限", "我同意授予这些临时权限"})


def plain_answer(resident_id, message, *, actor, repository):
    """Return the existing answer envelope and its grants, or leave an ordinary message alone.

    No issuer comes from message content. Delivery still signs as its
    authenticated User, checks that User's current rights, and atomically
    admits the one answer and its credentials. A quote selects an exact
    question; otherwise only the latest Agent reply can supply the scope.
    """
    parts = message.get("content_parts") or []
    if not parts or any(not isinstance(p, dict) or p.get("type") not in {"text", "context_reference"} for p in parts):
        return message, []
    # Page context may accompany any composer input. It cannot supply an
    # issuer, a scope or consent; only the person's text answers the card.
    original_text = "\n".join(str(p.get("text") or "") for p in parts if p.get("type") == "text")
    text = original_text.strip().rstrip("。.!！ ")
    if text not in APPROVALS or str(message.get("role") or "user") != "user":
        return message, []
    try:
        if actor_principal(actor).kind != "user":
            return message, []
    except ValueError:
        return message, []
    source_id = str((message.get("quote") or {}).get("message_id") or "")
    source = repository.message(resident_id, source_id) if source_id else repository.latest_message(resident_id, role="agent")
    if not source or source.get("role") != "agent":
        return message, []
    recipients = source.get("recipients") or []
    if recipients and {"kind": "user", "id": actor} not in recipients:
        return message, []
    questions = question_set(source)
    if not questions or any(not q.get("credentials") for q in questions["questions"]):
        return message, []
    source_id = source["message_id"]
    state = repository.question_set_state(resident_id, source_id)
    if state == "closed":
        raise ValueError("这组权限请求已关闭，请重新说明所需权限")
    # An answered source is deliberately retained: admission validates the
    # original author's identity and lending.replay cannot re-sign a revoked
    # or expired credential. It never reopens the question.
    answers = [{"question_index": q["question_index"], "answer": CREDENTIAL_APPROVE}
               for q in questions["questions"]]
    body = {**message, "message_id": questions["answer_message_id"], "content_parts": [
        {"type": "text", "text": original_text},
        {"type": ANSWER_PART, "source_message_id": source_id, "answers": answers,
         "plain_consent": {"text": original_text, "message_id": str(message.get("message_id") or "")}},
    ]}
    return body, approved_credentials(questions["questions"], answers)

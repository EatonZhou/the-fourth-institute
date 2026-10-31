"""Ask the people managing a Family what its Router or Steward does when no safe account is left.

When an operating role's account is confirmed short of reserve and no safe
account can take over, the facade asks in its Conversation, once per quota
window: continue on the current account's credits (the facade's manual quota
release, which its Router and Steward follow), wait for the reset, or add an
account. Anyone who may manage the facade answers. The answer stays in the
facade Conversation - the Router never routes it - and DeliverMessage then
carries out the choice (family_quota.answer_role_quota_question). The set
closes unanswered once the role has an account again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from management_common.async_questions import answered_set
from management_common.message_identity import derived_message_identity

BUSINESS_REF = "family-role-quota"
CONTINUE = "用当前账号的 credits 继续"
WAIT = "等额度重置"
ADD_ACCOUNT = "我去补账号"
OPTIONS = [CONTINUE, WAIT, ADD_ACCOUNT]


def _reset_text(account: Mapping[str, Any]) -> str:
    windows = (account.get("quota_reserve") or {}).get("windows") or []
    times = []
    for window in windows:
        value = window.get("resets_at")
        try:
            moment = (datetime.fromtimestamp(float(value), timezone.utc) if isinstance(value, (int, float))
                      else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            continue
        times.append(moment)
    if not times:
        return "未知"
    return max(times).astimezone().strftime("%m-%d %H:%M")


def ask(record: Mapping[str, Any], facade: Mapping[str, Any], account: Mapping[str, Any],
        remaining: float, epoch: str) -> str:
    """Post this quota window's question in the facade Conversation; return its message ID ("" when nobody can read it)."""

    from . import outward_messages, resident_conversation_stream
    from .family_owners import recipients

    facade_id = str(facade["resident_id"])
    message_id = derived_message_identity("family-role-quota", facade_id, epoch)
    repository = resident_conversation_stream.get_repository()
    if repository.message(facade_id, message_id) is not None:
        return message_id
    role = "Router" if (record.get("family") or {}).get("role") == "router" else "steward"
    try:
        waiting = int(repository.current_state(record["resident_id"]).get("queued_count") or 0)
    except KeyError:
        waiting = 0
    label = str(account.get("label") or account.get("account_id") or "当前账号")
    text = (f"Family 的 {role}（{record.get('title') or record['resident_id']}）所用账号 {label} "
            f"额度剩余 {remaining:g}%，没有剩余额度超过 10% 的账号可以接替，"
            f"{'有 ' + str(waiting) + ' 条工作在等它，' if waiting else ''}整个 Family 因此暂停。"
            f"额度预计 {_reset_text(account)} 重置。请选择接下来怎么办：")
    written = outward_messages.post_platform_notice(
        facade_id, recipients(record, creator_only=True), text, message_id=message_id,
        business_ref=f"{BUSINESS_REF}/{record['resident_id']}",
        questions=[{"title": "这个 Family 接下来怎么办？", "options": OPTIONS}],
    )
    return message_id if written.get("recipients") else ""


def close(facade_id: str, message_id: str) -> None:
    """The role has an account again: an unanswered question no longer applies."""

    if not facade_id or not message_id:
        return
    from . import resident_conversation_stream
    try:
        resident_conversation_stream.get_repository().close_question_set(facade_id, message_id, actor="portal")
    except (KeyError, ValueError):
        pass  # Answered, or gone with its Conversation.


def question_of(facade_id: str, message: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The facade's own quota question this user message answers; None for any other message."""

    source_id = answered_set(message)
    if not source_id:
        return None
    from . import resident_conversation_stream
    repository = resident_conversation_stream.get_repository()
    if not repository.message_business_ref(facade_id, source_id).startswith(BUSINESS_REF + "/"):
        return None
    return repository.message(facade_id, source_id)


def choice_of(answer: Mapping[str, Any] | None) -> str:
    """The option an answer to the quota question chose."""

    return next((str(entry.get("answer") or "").strip()
                 for part in (answer or {}).get("content_parts") or [] if part.get("type") == "async_question_answer"
                 for entry in part.get("answers") or []), "")


def post_result(facade_id: str, answer_id: str, actor: str, text: str) -> None:
    """Say in the facade Conversation what the answer did."""

    from . import outward_messages
    try:
        outward_messages.post_platform_notice(
            facade_id, [actor], text,
            message_id=derived_message_identity("family-role-quota-result", facade_id, answer_id),
            business_ref=f"{BUSINESS_REF}-result",
        )
    except (KeyError, PermissionError, ValueError):
        logging.getLogger(__name__).exception("Family role quota result was not posted on %s", facade_id)

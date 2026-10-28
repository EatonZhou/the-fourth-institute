"""Quote replies: a user message may quote one message of the Conversation it goes to (decision_c524c775e0be).

The sender names only the quoted message.  The platform checks that the
message shows in the same Conversation - in a Family facade's Conversation a
member reply shown there and the facade's record of a message it sent out
count too - and that the sender can read it, then fills in the quoted
message's author and the opening of its text.  The quote never changes after
that.  The Resident that receives the message reads the author and the
excerpt before the message's own text.
"""
from __future__ import annotations

from typing import Any, Mapping

from management_common.principal import MAX_QUOTE_TEXT, message_quote
from management_portal.execution.message_projection import quote_block

from . import family_facade, resident_access, resident_conversation_stream


def quote_text(message: Mapping[str, Any]) -> str:
    """The opening of a message's text, as much as a quote carries."""

    text = "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    ).strip()
    return text[:MAX_QUOTE_TEXT].rstrip()


def _shown(record: Mapping[str, Any], message_id: str) -> dict[str, Any] | None:
    """A message shown in a Resident's Conversation: one it holds, or on a facade a member reply shown there."""

    resident_id = str(record.get("resident_id") or "")
    found = resident_conversation_stream.get_repository().message(resident_id, message_id)
    if found is None and family_facade.is_facade(record):
        found = family_facade.projected_reply(record, message_id)
    return found


def quote_of(record: Mapping[str, Any], message_id: str) -> dict[str, str]:
    """The quote of one message shown in a Resident's Conversation; empty when it shows no such message with text."""

    shown = _shown(record, message_id) if message_id else None
    text = quote_text(shown) if shown else ""
    if not text:
        return {}
    author = str(shown.get("author_id") or "").strip()
    if not author:
        author = str(record.get("resident_id") or "") if shown.get("role") == "agent" else "portal"
    return {"message_id": message_id, "author_id": author, "text": text}


def accept(
    record: Mapping[str, Any],
    requested: object,
    *,
    reader: str,
    message_id: str,
    relay_source: tuple[str, str] | None = None,
    reply_source_message_id: str = "",
) -> dict[str, str]:
    """The quote a delivered user message carries, checked and filled in; empty when it quotes nothing.

    A retry keeps the quote accepted the first time.  A Family Router's relay
    that names no quote carries the facade message's, pointed at the member's
    own Conversation where it has the quoted message. A Family reply carries
    its received source separately; it authorizes only the matching outward
    record, without opening the rest of the facade Conversation.
    """

    resident_id = str(record.get("resident_id") or "")
    existing = resident_conversation_stream.get_repository().message(resident_id, message_id) if message_id else None
    kept = message_quote(existing) if existing else {}
    if requested is None or requested == {}:
        if relay_source is None:
            return {}
        return kept if existing else family_facade.relay_quote(relay_source, resident_id)
    if not isinstance(requested, Mapping) or set(requested) != {"message_id"}:
        raise ValueError("quote 只给出被引用消息的 message_id，作者和摘录由平台填写")
    quoted_id = str(requested.get("message_id") or "").strip()
    if not quoted_id or quoted_id == message_id:
        raise ValueError("quote.message_id 需要指向这段对话中的另一条消息")
    if kept.get("message_id") == quoted_id:
        return kept
    from . import outward_messages

    received_reply = bool(reply_source_message_id) and outward_messages.reply_target(
        reader, reply_source_message_id,
    ) == (resident_id, quoted_id)
    if not received_reply and not resident_access.resident_visible_to_actor(dict(record), reader):
        raise PermissionError("发送者不能读取被引用的消息")
    quote = quote_of(record, quoted_id)
    if not quote:
        raise ValueError(f"被引用的消息不在这段对话中，或没有正文：{quoted_id}")
    return quote


def with_quote(text: str, quote: Mapping[str, Any] | None) -> str:
    """A message as its Resident reads it: the quoted author and excerpt first, then the message itself."""

    if not quote:
        return text
    from . import resident_record_domain

    author = str(quote.get("author_id") or "")
    title = str((resident_record_domain.find_resident(author) or {}).get("title") or "") if author.startswith("resident_") else ""
    return f"{quote_block(quote, author_title=title)}\n\n{text}"

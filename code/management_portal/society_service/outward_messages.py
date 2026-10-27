"""Messages a Resident sends to users, and a Family's messages to the outside (decision_c524c775e0be).

A Resident sends to a user with the same ``DeliverMessage`` it uses for a
Resident.  The message starts no work: it is written into the sender's own
Conversation as the sender's message, naming the user in ``recipients``, and
the user sees that Conversation in the Inbox as unread (decision_b4e5ea4a47ec).
Whether to answer, and to whom, is up to whoever receives a message; nothing
is routed back automatically.

A Family shows the outside one identity (decision_44cf587c1721).  When a
member or the steward sends to someone outside its Family, the message goes
out as the facade of the outermost Family that holds the sender but not the
recipient, and the sender is recorded as ``initiated_by``.  A message to a
user is then written into that facade's Conversation; a message to a Resident
is delivered as the facade, and the facade's Conversation keeps a record of it
naming the recipient.  Messages inside a Family keep their sender.  Someone
who last wrote to the sender (or an inner facade) itself, rather than to the
facade, hears back from where it wrote.

A Resident that answers a message a facade sent it writes to the facade.
Sent during the turn that works on that message, or naming it in
``reply_to_message_id``, the answer quotes the facade's record of the
message, and the facade hands it straight to the member that wrote it.

The facade is only whom the message comes from: what it carries is still the
member's.  Its attachments and references are read with the member's own
access, as if the member had sent it, and never with the facade's.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Mapping

from management_common.actor_identity import canonical_actor_id
from management_common.message_identity import derived_message_identity
from management_common.operation_identity import current_operation_identity, operation_identity_scope
from management_common.principal import OperationIdentity, Principal, actor_principal, message_origin

from . import family_facade, family_storage, resident_access, resident_conversation_stream, resident_lineage
from .state_store import get_lineage_data_store

# A message to a user is read, not executed: it carries text and references to resources.
USER_MESSAGE_PART_TYPES = frozenset({"text", "resource_reference"})

# The (facade, member) pair of the delivery in which the platform has a facade speak for a member.
_SPEAKING: ContextVar[tuple[str, str] | None] = ContextVar("family_speaking_for", default=None)


def _find(resident_id: str) -> dict[str, Any]:
    from . import resident_record_domain

    return resident_record_domain.find_resident(resident_id) or {}


def _families_around(resident_id: str) -> list[dict[str, Any]]:
    with get_lineage_data_store().database.read() as connection:
        return family_storage.families_around(connection, resident_id, max_hops=family_facade.MAX_RELAY_HOPS)


def _last_direct_messages(party: str, resident_ids: list[str]) -> dict[str, str]:
    """When ``party`` itself last sent each Resident a message, not through a facade ("" if never)."""

    summaries = resident_conversation_stream.get_repository().conversation_summaries(
        resident_ids, party, excluded_business_ref=family_facade.RELAY_REF.pattern,
    )
    return {
        resident_id: str((summaries.get(resident_id) or {}).get("actor_last_user_message_at") or "")
        for resident_id in resident_ids
    }


def _moment(value: object) -> datetime | None:
    text = str(value or "").strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _last_written_to(party: str, identities: list[str]) -> str:
    """Which of ``identities`` ``party`` last wrote to itself, or "" if none.

    A Fork's copied history was written to its source before the Fork
    existed, so messages older than a Resident are not to that Resident.
    An outer identity wins a tie.
    """

    chosen, newest = "", None
    written = _last_direct_messages(party, identities)
    for identity in reversed(identities):
        at = _moment(written.get(identity))
        created = _moment(_find(identity).get("created_at"))
        if at is None or (created is not None and at < created):
            continue
        if newest is None or at > newest:
            chosen, newest = identity, at
    return chosen


def speaking_identity(sender: str, recipient: Mapping[str, str]) -> str:
    """Who a message from ``sender`` to ``recipient`` goes out as: the sender, or a facade around it."""

    sender_id = canonical_actor_id(sender)
    around = _families_around(sender_id)
    if not around:
        return sender_id
    recipient_id = str(recipient.get("id") or "")
    holding: set[str] = set()
    if recipient.get("kind") == "resident":
        holding = {item["family_id"] for item in _families_around(recipient_id)}
        with get_lineage_data_store().database.read() as connection:
            role = family_storage.resident_roles(connection, [recipient_id]).get(recipient_id, {})
        if role.get("role") == "facade":
            holding.add(role["family_id"])
    identities = [sender_id]
    for family in around:
        facade_id = family["facade_id"]
        if family["family_id"] in holding or not facade_id:
            break
        identities.append(facade_id)
    if len(identities) == 1:
        return sender_id
    # The recipient hears from where it last wrote itself: the sender or an
    # inner facade it wrote to more recently than to the outer facade.
    return _last_written_to(recipient_id, identities) or identities[-1]


def identities_of(resident_id: str) -> list[str]:
    """A Resident and the facades of the Families around it, innermost first."""

    return [resident_id, *(family["facade_id"] for family in _families_around(resident_id))]


def speaks_for(facade_id: str, member_id: str) -> bool:
    """Whether ``facade_id`` is the facade of a Family that holds ``member_id``."""

    member_id = canonical_actor_id(member_id)
    return bool(facade_id and member_id) and any(
        family["facade_id"] == facade_id for family in _families_around(member_id)
    )


@contextmanager
def _speaking_for(speaker: str, sender: str) -> Iterator[None]:
    """Deliver as facade ``speaker`` for member ``sender``: the facade's operation, initiated by the member."""

    token = _SPEAKING.set((speaker, sender))
    try:
        with operation_identity_scope(OperationIdentity.for_actor(speaker, initiated_by=sender)):
            yield
    finally:
        _SPEAKING.reset(token)


def speaking_for(actor: str) -> str:
    """The member the platform has facade ``actor`` sending for right now; "" when it sends for no one.

    Only a Family's message out makes a facade speak for a member, and what
    the message carries is read with that member's own access.  Any other
    operation, even one a member initiated, is read with its actor's.
    """

    current = _SPEAKING.get()
    identity = current_operation_identity()
    if current is None or identity is None or current[0] != canonical_actor_id(actor):
        return ""
    if (identity.principal.id, identity.initiated_by.id) != current:
        return ""  # another operation inside the delivery speaks for itself
    return current[1]


def _origin(speaker: str, sender: str) -> dict[str, object]:
    if speaker != sender:
        return message_origin(speaker, initiated_by=sender)
    identity = current_operation_identity()
    return message_origin(speaker, initiated_by=identity.initiated_by if identity is not None else "")


def _write(resident_id: str, message: dict[str, Any]) -> dict[str, Any]:
    """Add a message the Resident (or Family) sends to its own Conversation; a retry finds it there."""

    from management_portal.execution.agent_data_store import utc_now

    repository = resident_conversation_stream.get_repository()
    existing = repository.message(resident_id, message["message_id"])
    now = utc_now()
    message["created_at"] = str((existing or {}).get("created_at") or now)
    return repository.append_message(resident_id, message, activated_at=now)


def deliver_to_user(
    sender: str,
    user_id: str,
    content_parts: object,
    *,
    message_id: str,
    business_ref: str = "",
) -> dict[str, Any]:
    """Send a user a message from a Resident, written in the Conversation it is sent from."""

    sender_id = canonical_actor_id(sender)
    if actor_principal(sender_id).kind != "resident":
        raise PermissionError("只有 Resident 可以给用户发消息")
    try:
        recipient = Principal("user", str(user_id or "").strip()).to_dict()
    except ValueError as exc:
        raise ValueError(f"user_id 不是有效的用户 ID：{user_id}") from exc
    parts = content_parts if isinstance(content_parts, list) else []
    if not any(isinstance(part, Mapping) and part.get("type") == "text" and str(part.get("text") or "").strip()
               for part in parts):
        raise ValueError("发给用户的消息需要非空文字")
    unsupported = sorted({
        str(part.get("type") or "") if isinstance(part, Mapping) else "?"
        for part in parts
    } - USER_MESSAGE_PART_TYPES)
    if unsupported:
        raise ValueError("发给用户的消息只包含文字和资源引用：" + "、".join(unsupported))
    speaker = speaking_identity(sender_id, recipient)
    record = _find(speaker)
    if not record:
        raise KeyError("resident not found")
    if record.get("deleted_at"):
        raise ValueError(f"{speaker} 已删除，不能发消息")
    # The message stays in this Conversation; its reader must be able to open it.
    if not resident_access.resident_visible_to_actor(record, recipient["id"]):
        raise PermissionError(f"用户 {recipient['id']} 不存在或不能查看 {speaker} 的对话")
    message: dict[str, Any] = {
        "message_id": message_id,
        "role": "agent",
        "author_id": speaker,
        "origin": _origin(speaker, sender_id),
        "recipients": [recipient],
        "content_parts": [dict(part) for part in parts],
    }
    if business_ref:
        message["business_ref"] = str(business_ref).strip()[:500]
    written = _write(speaker, message)
    return {
        "resident_id": speaker,
        "message_id": message_id,
        "message": written,
        "sender_id": speaker,
        "recipients": [recipient],
    }


def post_platform_notice(
    resident_id: str,
    user_ids: list[str],
    text: str,
    *,
    message_id: str,
    business_ref: str = "",
) -> dict[str, Any]:
    """Portal writes to users in a Resident's name, starting no work (decision_15f7df737ed5).

    The platform signs with a Resident the users can talk to, so an answer
    reaches it; ``origin.initiated_by`` is the Portal service.  Inside a
    Family the message goes out as its facade, like any message to a user.
    One message names every user who can view that Conversation; the others
    come back as ``unreached`` for another channel.  A message already
    written under ``message_id`` stays as it is.
    """

    users = [str(user) for user in dict.fromkeys(user_ids) if str(user or "").strip()]
    if not users:
        return {"resident_id": "", "message_id": message_id, "recipients": [], "unreached": []}
    speaker = speaking_identity(canonical_actor_id(resident_id), Principal("user", users[0]).to_dict())
    record = _find(speaker)
    if not record or record.get("deleted_at"):
        return {"resident_id": "", "message_id": message_id, "recipients": [], "unreached": users}
    readers = [user for user in users if resident_access.resident_visible_to_actor(record, user)][:20]
    unreached = [user for user in users if user not in readers]
    if not readers:
        return {"resident_id": speaker, "message_id": message_id, "recipients": [], "unreached": unreached}
    recipients = [Principal("user", user).to_dict() for user in readers]
    if resident_conversation_stream.get_repository().message(speaker, message_id) is None:
        message: dict[str, Any] = {
            "message_id": message_id,
            "role": "agent",
            "author_id": speaker,
            "origin": message_origin(speaker, initiated_by="portal"),
            "recipients": recipients,
            "content_parts": [{"type": "text", "text": text}],
        }
        if business_ref:
            message["business_ref"] = str(business_ref).strip()[:500]
        _write(speaker, message)
    return {"resident_id": speaker, "message_id": message_id, "recipients": recipients, "unreached": unreached}


def outward_record_id(facade_id: str, message_id: str) -> str:
    """The facade's record of one message it sent out to a Resident."""

    return derived_message_identity("family-outward", facade_id, message_id)


# -- replies to a Family's message out (decision_44cf587c1721) ---------------------------


def reply_target(resident_id: str, message_id: str) -> tuple[str, str] | None:
    """The facade that sent out a message a Resident received, and the facade's record of it; None when no facade did.

    The message may have reached the Resident through other Families'
    Routers: the facade's record names whom it sent the message to.
    """

    repository = resident_conversation_stream.get_repository()
    if not resident_id or not message_id or repository.message(resident_id, message_id) is None:
        return None
    for holder, held_id in [(resident_id, message_id), *family_facade.relay_sources_of(resident_id, message_id)]:
        facade_id = str((repository.message(holder, held_id) or {}).get("author_id") or "")
        if not facade_id.startswith("resident_"):
            continue
        record_id = outward_record_id(facade_id, held_id)
        record = repository.message(facade_id, record_id) or {}
        if any(isinstance(item, Mapping) and item.get("id") == holder for item in record.get("recipients") or []):
            return facade_id, record_id
    return None


def _turn_messages(resident_id: str) -> list[str]:
    """The messages a Resident's running turn works on: its own, those merged into it and those sent into it."""

    from . import resident_execution_work

    record = _find(resident_id)
    job = resident_execution_work.read(record) if record else {}
    if not job or not resident_execution_work.is_active(job) or resident_execution_work.completion_applied(job):
        return []
    items = [job, *(job.get("mailbox_members") or []), *resident_execution_work.send_now_inputs(job)]
    return [mid for mid in dict.fromkeys(
        str(item.get("user_message_id") or "") for item in items if isinstance(item, Mapping)
    ) if mid]


def reply_quote(sender_id: str, recipient_id: str, reply_to: str = "") -> dict[str, str] | None:
    """The quote that makes a message to a facade the reply to one the facade sent the sender.

    ``reply_to`` names the message the sender received.  Without it, a
    message sent during the turn that works on a message from this facade
    replies to it, when exactly one of the turn's messages came from there.
    The facade then hands the reply straight to the member that wrote the
    message, as for any quote of its record.
    """

    if reply_to:
        target = reply_target(sender_id, reply_to)
        if target is None:
            raise ValueError(f"reply_to_message_id 需要是 Family 门面发给你的消息：{reply_to}")
        if target[0] != recipient_id:
            raise ValueError(f"这条消息由 {target[0]} 发来，回复它要发给 {target[0]}")
        return {"message_id": target[1]}
    found = {
        target[1] for item in _turn_messages(sender_id)
        if (target := reply_target(sender_id, item)) is not None and target[0] == recipient_id
    }
    return {"message_id": found.pop()} if len(found) == 1 else None


def reply_hint(resident_id: str, actor: str, message_id: str, business_ref: str = "") -> str:
    """How the Resident receiving a message a facade sent out answers it; "" for any other message.

    While the message is delivered, the facade is speaking for a member or a
    Router relays a message some facade sent out; afterwards the facade's
    record shows it.
    """

    if not message_id:
        return ""
    if speaking_for(actor):
        facade_id = canonical_actor_id(actor)
    else:
        source = family_facade.parse_relay_mark(business_ref) if business_ref else None
        target = reply_target(*source) if source is not None else reply_target(resident_id, message_id)
        facade_id = target[0] if target is not None else ""
    if not facade_id:
        return ""
    return (
        f"这条消息由 Family 门面 {facade_id} 代它的成员发出。本轮用 DeliverMessage 发给 {facade_id}，"
        "平台会把它记成对这条消息的回复，直接交给写它的成员；"
        f"以后的轮次再回复它，DeliverMessage 加参数 reply_to_message_id: {message_id}。"
    )


def _as_reply(payload: Mapping[str, Any], sender_id: str, recipient_id: str, reply_to: str, *, plain: bool) -> Mapping[str, Any]:
    """The payload with the quote that makes it a reply; unchanged when it names its own quote or replies to nothing."""

    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    carries_none = "quote" in message or payload.get("activation_policy") == "attach_to_next" or not plain
    if reply_to and carries_none:
        raise ValueError("reply_to_message_id 不和 quote 同用；info 消息、Router 转交和授权调用的输入也不带它")
    quote = None if carries_none else reply_quote(sender_id, recipient_id, reply_to)
    return {**payload, "message": {**message, "quote": quote}} if quote else payload


def deliver_to_resident(
    payload: Mapping[str, Any], sender: str, *, delivery_context: Any, reply_to: str = "",
) -> dict[str, Any]:
    """Deliver to a Resident as the sender, or as its Family's facade when the recipient is outside the Family.

    A Family Router's relay keeps its original sender, and so does an input
    scoped to an authorized invocation.  A message to a facade that answers
    what the facade sent the sender is delivered as a reply to it.
    """

    from . import resident_input_scope, resident_protocol

    sender_id = canonical_actor_id(sender)
    recipient_id = str(payload.get("resident_id") or "").strip()
    message = payload.get("message") if isinstance(payload.get("message"), Mapping) else {}
    message_id = str(message.get("message_id") or "")
    plain = (
        family_facade.parse_relay_mark(getattr(delivery_context, "business_ref", "")) is None
        and not resident_input_scope.permits(recipient_id, sender_id, message_id)
    )
    payload = _as_reply(payload, sender_id, recipient_id, str(reply_to or "").strip(), plain=plain)
    speaker = sender_id
    if plain:
        speaker = speaking_identity(sender_id, {"kind": "resident", "id": recipient_id})
    if speaker == sender_id:
        return {**resident_protocol.deliver_resident_input(dict(payload), sender, delivery_context=delivery_context),
                "sender_id": sender_id}
    # The Family speaks, but only where the sender itself may send.
    target = _find(recipient_id)
    if not target:
        raise KeyError("resident not found")
    if str(target.get("personal_owner_id") or "").strip():
        # Anyone may message a personal AMA; nothing else of it opens (decision_bd5217d08c09).
        if not resident_access.resident_deliverable_to_actor(target, sender_id):
            raise KeyError("resident not found")
    else:
        resident_access.ensure_resident_visible_to_actor(target, sender_id)
        if not resident_access.can_manage_resident(target, sender_id):
            raise resident_access.manage_refusal(target)
    with _speaking_for(speaker, sender_id):
        result = resident_protocol.deliver_resident_input(dict(payload), speaker, delivery_context=delivery_context)
    delivered = resident_conversation_stream.get_repository().message(recipient_id, message_id) or {}
    _write(speaker, {
        "message_id": outward_record_id(speaker, message_id),
        "role": "agent",
        "author_id": speaker,
        "origin": message_origin(speaker, initiated_by=sender_id),
        "recipients": [{"kind": "resident", "id": recipient_id}],
        "content_parts": [dict(part) for part in delivered.get("content_parts") or message.get("content_parts") or []
                          if isinstance(part, Mapping)],
    })
    return {**result, "sender_id": speaker}

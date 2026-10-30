"""Chatrooms: conversations among Users that can call on any Resident (decision_a6bebf4ac283).

A Chatroom's members are Users.  Its record -- title, members, each member's
read position, the Residents called into it and the delivery records -- lives
in the public-discussion state database; its messages live in AgentData under
the Chatroom's own stream (``agent_id`` is the Chatroom ID).  Every write to a
Chatroom happens inside the record transaction, so message positions and the
record never disagree.

Writing ``@resident_<id>`` in a message calls that Resident: the message and
the Chatroom ID are delivered with ``DeliverMessage`` under the author's own
identity.  The Resident's reply stays in its own Conversation and is shown in
the Chatroom as its message, either when SocietyService reports the input's
terminal or when a reader finds the delivery still open.
"""
from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from management_common.actor_identity import canonical_actor_id, same_actor
from management_common.message_identity import derived_message_identity
from management_common.postgres_database import atomic_transaction

from .chatroom_store import ChatroomState
from .conversation_receipt_store import VersionConflict
from .mailbox_sources import MailboxSource
from . import chatroom_store

LOGGER = logging.getLogger(__name__)


SCHEMA = "tfi.chatroom.v2"
CHATROOM_ID_RE = re.compile(r"chatroom_[0-9a-f]{12}")
# The Resident inputs a Chatroom delivers carry ``<chatroom_id>/<message_id>`` as their
# business_ref; SocietyService reports their progress and end here (portal_features
# ``resident.inputs``).  ForwardMessages reads an item naming a Chatroom with
# ``forward_source`` (``conversation.forwarding``).
BUSINESS_REF_PREFIX = "chatroom_"
FORWARD_FIELD = "chatroom_id"
RESIDENT_ID_RE = re.compile(r"resident_[0-9a-f]{12}")
UPLOAD_ID_RE = re.compile(r"upload_[0-9a-f]+")
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@(resident_[0-9a-f]{12})(?![0-9a-z_])")
MAX_MESSAGE_CHARS = 8000
MAX_ATTACHMENTS = 10
MAX_TITLE_CHARS = 80
MAX_MEMBERS = 50
MAX_MENTIONS = 5
RECENT_LIMIT = 20
MAX_PAGE_SIZE = 200
DELIVERY_HISTORY_LIMIT = 100
PORTAL_AUTHOR = "portal"
REACTION_EMOJIS = ("👍", "❤️", "😂", "🎉", "👀", "✅")
MEDIA_TYPES = frozenset({"image", "file"})
OPEN_DELIVERY_STATES = frozenset({"queued", "delivered"})
# A queued intent younger than this is still being sent by the request that posted it.
QUEUED_RETRY_AFTER_SECONDS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _store():
    # Chatrooms belong to the public-discussion domain and share its schema.
    return ChatroomState()


def _agent_data():
    from management_portal.execution.agent_data_store import get_agent_data_store

    return get_agent_data_store()


def _actor(value: object) -> str:
    actor = canonical_actor_id(value)
    if not actor:
        raise PermissionError("需要经过认证的调用者")
    return actor


def _chatroom_id(value: object) -> str:
    chatroom_id = str(value or "").strip().lower()
    if not CHATROOM_ID_RE.fullmatch(chatroom_id):
        raise ValueError("chatroom_id 必须是 chatroom_ 加 12 位小写十六进制码")
    return chatroom_id


def _message_text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text" and part.get("text")
    ).strip()


def _preview(message: Mapping[str, Any]) -> str:
    if message.get("retracted_at"):
        return "[消息已撤回]"
    collapsed = " ".join(_message_text(message).split())
    if not collapsed:
        media = [part for part in message.get("content_parts") or [] if isinstance(part, Mapping) and part.get("type") in MEDIA_TYPES]
        collapsed = "[图片]" if media and media[0].get("type") == "image" else ("[附件]" if media else "")
    return collapsed if len(collapsed) <= 120 else collapsed[:119] + "…"


def _is_member(room: Mapping[str, Any], principal_id: object) -> bool:
    return any(same_actor(member, principal_id) for member in room.get("members") or [])


def _member_id(room: Mapping[str, Any], principal_id: object) -> str:
    return next((member for member in room.get("members") or [] if same_actor(member, principal_id)), "")


def _called_residents(room: Mapping[str, Any]) -> list[str]:
    return list(room.get("called_residents") or [])


# -- access ---------------------------------------------------------------------------


def _require_member(room: Mapping[str, Any] | None, actor: str, *, read: bool = False) -> dict[str, Any]:
    """Membership is conversation metadata; access is decided at the ISA entry."""
    if room is None:
        raise KeyError("聊天室不存在")
    _actor(actor)
    return dict(room)


def _require_reader(room: Mapping[str, Any] | None, actor: str) -> dict[str, Any]:
    return _require_member(room, actor, read=True)


def can_read(chatroom_id: object, actor: object) -> bool:
    """Whether the actor may read the Chatroom and the attachments sent to it."""

    try:
        room = _store().get(_chatroom_id(chatroom_id))
        _require_reader(room, canonical_actor_id(actor))
    except (KeyError, ValueError):
        return False
    return True


def in_mailbox(chatroom_id: object, actor: object) -> bool:
    """A Chatroom is in its members' Mailboxes; checked without assembling one."""

    try:
        room = _store().get(_chatroom_id(chatroom_id), viewer=_actor(actor), delivery_ids=())
    except (PermissionError, ValueError):
        return False
    return room is not None and _is_member(room, actor)


def _validated_users(principals: Iterable[object]) -> list[str]:
    """Registered, active Users only: Residents are called with @, never added."""

    users = _portal_users()
    validated: list[str] = []
    for principal in principals:
        principal_id = canonical_actor_id(principal)
        if not principal_id:
            continue
        if RESIDENT_ID_RE.fullmatch(principal_id.lower()):
            raise ValueError("聊天室成员只能是用户；在消息中 @ Resident 即可召唤它")
        user = next((item for item in users if same_actor(item.get("username"), principal_id)), None)
        if user is None:
            raise ValueError(f"用户不存在：{principal_id}")
        if str(user.get("status") or "active") != "active":
            raise ValueError(f"用户已停用：{principal_id}")
        username = str(user["username"])
        if not any(same_actor(item, username) for item in validated):
            validated.append(username)
    return validated


def _portal_users() -> list[dict[str, Any]]:
    """Every registered Portal User: the accounts of the Portal password authority."""

    from management_portal.portal_access_control import PortalCredentialStore, get_portal_access_store

    return get_portal_access_store().list_users(PortalCredentialStore().load().keys())


# -- public projections ------------------------------------------------------------


def _unread_count(room: Mapping[str, Any], user: str) -> int:
    read_through = int((room.get("read_through") or {}).get(user.casefold()) or 0)
    return max(0, int(room.get("last_ordinal") or 0) - read_through)


def _viewer_state(room: Mapping[str, Any], user: str) -> dict[str, Any]:
    key = user.casefold()
    unread = _unread_count(room, user)
    muted = bool((room.get("muted_by") or {}).get(key))
    read = int((room.get("read_through") or {}).get(key) or 0)
    mentions = [dict(item) for item in (room.get("user_mentions") or {}).get(key, []) if item["ordinal"] > read]
    return {"unread_count": unread, "muted": muted, "unread_mentions": mentions,
            "unread_mention_count": len(mentions), "attention_unread": bool(unread and (not muted or mentions)),
            # The member's Mailbox row revision (conversation_receipt_store).
            "read_revision": int((room.get("read_revisions") or {}).get(key) or 0)}


def _public_room(room: Mapping[str, Any], actor: str) -> dict[str, Any]:
    member = _is_member(room, actor)
    return {
        "chatroom_id": room["chatroom_id"],
        "title": room.get("title") or "",
        "members": list(room.get("members") or []),
        "called_residents": _called_residents(room),
        "created_by": room.get("created_by") or "",
        "created_at": room.get("created_at") or "",
        "updated_at": room.get("updated_at") or "",
        "last_message": dict(room.get("last_message") or {}),
        **(_viewer_state(room, actor) if member else {"unread_count": 0}),
        "permissions": {
            "can_post": bool(actor), "can_rename": bool(actor), "can_add_members": bool(actor), "can_leave": member,
            "can_delete": bool(actor),
        },
    }


def _public_message(message: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    result = {
        key: message[key]
        for key in ("message_id", "role", "author_id", "content_parts", "created_at", "reply_to_message_id", "quote", "edited_at", "retracted_at")
        if key in message
    }
    result["ordinal"] = int(ordinal)
    result["version"] = str(message.get("segment_id") or "")
    return result


def _message_quote(message: Mapping[str, Any] | None) -> dict[str, str]:
    from management_portal.society_service.message_quotes import quote_text

    text = ("这条消息已撤回" if message.get("retracted_at") else quote_text(message)) if message else ""
    if not text:
        return {}
    return {"message_id": str(message["message_id"]), "author_id": str(message.get("author_id") or PORTAL_AUTHOR), "text": text}


def resource_summary(chatroom_id: object) -> dict[str, Any]:
    """Canonical Chatroom summary; the calling ISA instruction owns authorization."""

    clean_id = _chatroom_id(chatroom_id)
    room = _store().get(clean_id)
    if room is None:
        raise KeyError(f"聊天室不存在：{clean_id}")
    return _resource_summary(room)


def resource_records():
    """Every Chatroom as the permission system places it (``permission.records`` hook)."""
    return [_permission_record(room) for room in _store().list() if room.get("chatroom_id")]


def _permission_record(room):
    return {"resource_id": room["chatroom_id"], "resource_type": "chatroom",
            "created_by": room.get("created_by"), "members": room.get("members") or []}


def _resource_summary(room):
    return {
        "title": room.get("title") or "、".join(room.get("members") or []),
        "members": list(room.get("members") or []),
        "created_by": room.get("created_by") or "",
        "created_at": room.get("created_at") or "",
        "updated_at": room.get("updated_at") or "",
    }


# -- notifications -----------------------------------------------------------------


def _publish(room: Mapping[str, Any], *, fields: Iterable[str], inbox: bool = True) -> None:
    """Wake the members' pages; reads stay authoritative, so a lost wake only delays them.

    A called Resident's progress wakes only the Chatroom view: the Inbox lists
    messages, and progress is not a message.
    """

    try:
        from management_portal.notifications import business_projection_subject, get_notification_publisher

        get_notification_publisher().publish(
            business_projection_subject("chatroom", room["chatroom_id"]),
            {
                "kind": "changed",
                "actors": sorted({member.casefold() for member in room.get("members") or []}),
                "fields": sorted(set(fields)),
            },
        )
    except Exception:  # noqa: BLE001 - notifications only wake readers
        LOGGER.warning("Chatroom %s change notification not published", room.get("chatroom_id"), exc_info=True)
    if inbox:
        _publish_inbox(room.get("members") or [])


def _publish_inbox(users: Iterable[str]) -> None:
    """Wake these Users' Inbox, where each Chatroom is one of their conversations."""

    clean = sorted({str(user) for user in users if str(user)})
    if not clean:
        return
    try:
        import sys

        # A Portal Web process drops the users' cached Inbox so the next read sees this change.
        web = sys.modules.get("management_portal.web_read_caches")
        for user in clean if web is not None else ():
            web._forget_cached_inbox(user)
        from management_portal.notifications import business_projection_subject, get_notification_publisher

        get_notification_publisher().publish(
            business_projection_subject("agent-conversation-index", "current"),
            {"kind": "changed", "actors": sorted({user.casefold() for user in clean}), "fields": ["chatroom"]},
        )
    except Exception as exc:  # noqa: BLE001 - notifications only wake readers
        from .http_errors import failure_frames

        LOGGER.warning("Chatroom Inbox wake-up failed for %d users: %s; frames=%s", len(clean), type(exc).__name__,
                       failure_frames(exc))
        return


# -- Inbox -------------------------------------------------------------------------


INBOX_KEY_PREFIX = "chatroom:"


def inbox_entries(actor: object) -> list[dict[str, Any]]:
    """The viewer's Chatrooms as Inbox conversations, unread as the Chatroom counts it.

    A Chatroom without messages yet is not shown.  The entry's version is its
    newest message, so a newer message makes an Inbox read toggle fail instead
    of marking unseen messages read.
    """

    from management_portal.conversation_completion import completion_signature

    viewer = canonical_actor_id(actor)
    if not viewer:
        return []
    entries: list[dict[str, Any]] = []
    for room in _store().list(actor=viewer):
        last = room.get("last_message") or {}
        if not _is_member(room, viewer) or not last.get("message_id"):
            continue
        chatroom_id = room["chatroom_id"]
        author = str(last.get("author_id") or "")
        preview = str(last.get("preview") or "")
        at = str(last.get("created_at") or room.get("updated_at") or "")
        unread_count = _unread_count(room, viewer)
        entries.append({
            "key": f"{INBOX_KEY_PREFIX}{chatroom_id}",
            "kind": "chatroom",
            "chatroom_id": chatroom_id,
            "title": room.get("title") or "、".join(room.get("members") or []) or chatroom_id,
            "subtitle": f"{author}：{preview}" if author and author != PORTAL_AUTHOR else preview,
            "surface": "lineage",
            "surface_label": "Chatroom",
            "url": f"/lineage?chatroom={chatroom_id}",
            "updated_at": at,
            "status": "idle",
            "completion_signature": completion_signature({
                "schema_version": 1,
                "request_actor": author,
                "completion_id": str(last["message_id"]),
                "input_message_id": "",
                "completed_at": at,
            }),
            "unread": unread_count > 0,
            **_viewer_state(room, viewer),
        })
    return entries


def set_unread(actor: object, chatroom_id: object, unread: bool, *, expected_revision: object = None) -> dict[str, Any]:
    """The Inbox read toggle: read reads the whole Chatroom; unread leaves its newest message unread.

    ``expected_revision`` is the member row revision the caller saw. It is
    compared under the room lock, so a message posted since refuses the change
    instead of being read unseen.
    """

    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    with _Transaction(clean_id, viewer=viewer, delivery_ids=()) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        current = _viewer_state(room, viewer)
        if expected_revision is not None and str(expected_revision) != str(current["read_revision"]):
            raise VersionConflict(current)
        last = int(room.get("last_ordinal") or 0)
        if unread:
            positions = room.setdefault("read_through", {})
            positions[viewer.casefold()] = min(int(positions.get(viewer.casefold()) or 0), max(0, last - 1))
            latest = room.get("last_message") or {}
            if latest and any(same_actor(viewer, user) for user in latest.get("mentioned_users", [])):
                pending = room.setdefault("user_mentions", {}).setdefault(viewer.casefold(), [])
                if not any(item["ordinal"] == last for item in pending):
                    pending.append({"message_id": latest["message_id"], "ordinal": last})
        else:
            _mark_read(room, viewer, last)
        rooms[clean_id] = room
    _publish_inbox([viewer])
    return {"chatroom_id": clean_id, **_viewer_state(room, viewer)}


def _mailbox_set_unread(actor: object, chatroom_id: object, unread: bool, *, expected_revision: object = None) -> dict[str, Any]:
    room = set_unread(actor, chatroom_id, unread, expected_revision=expected_revision)
    return {"unread": room["unread_count"] > 0, "read_revision": room["read_revision"]}


# A Chatroom is a conversation in its members' Mailboxes and moves their read
# positions itself, in their member rows.
MAILBOX_SOURCE = MailboxSource(INBOX_KEY_PREFIX, inbox_entries, conversation=True,
                               set_unread=_mailbox_set_unread, contains=in_mailbox)


# -- record transactions -----------------------------------------------------------


@contextmanager
def _Transaction(chatroom_id, *, viewer=None, delivery_ids=None, permissions=False):
    """Commit the stream, room metadata and change feed together under one lock.

    With ``permissions`` the room's place and its members' entries in the
    permission instance commit in the same transaction (decision_b820faf42218 §5.7).
    """
    domain = chatroom_store.database()
    domain.ensure()
    agent_data = _agent_data()
    store = _store()
    with atomic_transaction(domain.database, agent_data.database):
        with store.transaction(chatroom_id) as connection:
            previous = store.get(chatroom_id, viewer=viewer, delivery_ids=delivery_ids)
            rooms = {chatroom_id: deepcopy(previous)} if previous else {}
            yield rooms
            if set(rooms) - {chatroom_id}:
                raise ValueError("A Chatroom transaction may only write its own room")
            store.save(chatroom_id, rooms.get(chatroom_id), previous)
            if permissions:
                _sync_permissions(rooms.get(chatroom_id) or previous, deleted=chatroom_id not in rooms, connection=connection)


def _append(room: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """Add one message to the Chatroom stream; the caller holds the record transaction."""

    message.setdefault("created_at", _now())
    agent_data = _agent_data()
    stored = agent_data.display_chat_message(room["chatroom_id"], message)
    ordinal = agent_data.chat_message_ordinal(room["chatroom_id"], stored["message_id"])
    if ordinal is None:
        raise RuntimeError("Chatroom message was not displayed")
    summary = {
        "message_id": stored["message_id"],
        "author_id": str(stored.get("author_id") or ""),
        "role": stored["role"],
        "preview": _preview(stored),
        "created_at": str(stored.get("created_at") or message["created_at"]),
        "ordinal": ordinal,
    }
    if ordinal >= int(room.get("last_ordinal") or 0):
        room["last_ordinal"] = ordinal
        room["last_message"] = summary
    room["updated_at"] = summary["created_at"]
    return _public_message(stored, ordinal)


def _notice(room: dict[str, Any], key: str, text: str) -> dict[str, Any]:
    return _append(room, {
        "message_id": derived_message_identity("chatroom-notice", room["chatroom_id"], key),
        "role": "system",
        "author_id": PORTAL_AUTHOR,
        "content_parts": [{"type": "text", "text": text}],
    })


def _mark_read(room: dict[str, Any], user: str, ordinal: int) -> None:
    positions = room.setdefault("read_through", {})
    positions[user.casefold()] = max(int(positions.get(user.casefold()) or 0), int(ordinal))
    mentions = room.get("user_mentions") or {}
    if user.casefold() in mentions:
        mentions[user.casefold()] = [item for item in mentions[user.casefold()] if item["ordinal"] > positions[user.casefold()]]


# -- Chatroom lifecycle ------------------------------------------------------------


def _clean_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"聊天室名称最多 {MAX_TITLE_CHARS} 个字符")
    return title


def list_chatrooms(actor: object) -> list[dict[str, Any]]:
    """The viewer's Chatrooms, newest activity first, each with the viewer's unread count."""

    viewer = _actor(actor)
    rooms = [room for room in _store().list(actor=viewer) if _is_member(room, viewer)]
    rooms.sort(key=lambda room: str(room.get("updated_at") or ""), reverse=True)
    return [_public_room(room, viewer) for room in rooms]


def get_chatroom(actor: object, chatroom_id: object) -> dict[str, Any]:
    viewer = _actor(actor)
    room = _require_reader(_store().get(_chatroom_id(chatroom_id)), viewer)
    return {**_public_room(room, viewer), "active_deliveries": _active_deliveries(room)}


def _active_deliveries(room: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The Residents still working on a message of this Chatroom, and how far each has got.

    Read like the reply, with the sender's own access: ``GetInputState`` with
    ``include_progress`` gives the place in the Resident's queue while the
    message waits, and the work phase and live activities while it runs - the
    progress a Family facade shows under a relayed request.  Intermediate
    replies are left out; the reply shows once the Resident finishes.
    """

    active = []
    for delivery in _open_deliveries(room):
        item = {
            "id": str(delivery["id"]),
            "resident_id": str(delivery["resident_id"]),
            "trigger_message_id": str(delivery["trigger_message_id"]),
            "state": "delivering",
        }
        if delivery.get("status") == "delivered":
            try:
                result = _execute("GetInputState", {
                    "resident_id": delivery["resident_id"],
                    "message_id": delivery["input_message_id"],
                    "include_progress": True,
                }, str(delivery["delivered_by"]))
            except Exception:  # noqa: BLE001 - one unreadable Resident must not hide the others
                LOGGER.warning("Chatroom %s delivery %s: input state of %s unreadable", room.get("chatroom_id"),
                               delivery["id"], delivery["resident_id"], exc_info=True)
                continue
            input_state = result.get("input_state") if isinstance(result, Mapping) else None
            input_state = input_state if isinstance(input_state, Mapping) else {}
            state = str(input_state.get("state") or "")
            if state not in {"queued", "running"}:
                # Finished: reading the messages brings the reply or the reason back.
                continue
            progress = input_state.get("progress") if isinstance(input_state.get("progress"), Mapping) else {}
            item.update({key: progress[key] for key in ("ahead", "work_state", "work_phase", "activities") if key in progress})
            item["state"] = str(progress.get("state") or state)
        active.append(item)
    return active


def create_chatroom(actor: object, members: object, title: object = "") -> dict[str, Any]:
    from management_common.resource_identity import get_resource_type_registry

    viewer = _actor(actor)
    others = [user for user in _validated_users(members if isinstance(members, list) else []) if not same_actor(user, viewer)]
    creator = _validated_users([viewer])[0]
    if len(others) + 1 > MAX_MEMBERS:
        raise ValueError(f"聊天室最多 {MAX_MEMBERS} 位成员")
    now = _now()
    room = {
        "schema": SCHEMA,
        "chatroom_id": get_resource_type_registry().new_id("chatroom"),
        "title": _clean_title(title),
        "created_by": creator,
        "created_at": now,
        "updated_at": now,
        "members": [creator, *others],
        "read_through": {},
        "last_ordinal": 0,
        "last_message": {},
        "called_residents": [],
        "deliveries": [],
    }
    with _Transaction(room["chatroom_id"], permissions=True) as rooms:
        rooms[room["chatroom_id"]] = room
        notice = _notice(room, "created", f"{creator} 创建了聊天室")
        _mark_read(room, creator, notice["ordinal"])
    _publish(room, fields=["members", "messages"])
    return _public_room(room, viewer)


def update_chatroom(actor: object, chatroom_id: object, *, title: object = None, add_members: object = None, muted: object = None) -> dict[str, Any]:
    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    additions = _validated_users(add_members if isinstance(add_members, list) else [])
    clean_title = _clean_title(title) if title is not None else None
    personal_only = muted is not None and title is None and add_members is None
    with _Transaction(clean_id, viewer=viewer if personal_only else None, delivery_ids=() if personal_only else None,
                      permissions=not personal_only) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        if muted is not None:
            if not isinstance(muted, bool) or not _is_member(room, viewer):
                raise ValueError("只有聊天室成员可以设置自己的消息免打扰，muted 必须是布尔值")
            room.setdefault("muted_by", {})[viewer.casefold()] = muted
        joining = [user for user in additions if not _is_member(room, user)]
        if len(room["members"]) + len(joining) > MAX_MEMBERS:
            raise ValueError(f"聊天室最多 {MAX_MEMBERS} 位成员")
        now = _now()
        if clean_title is not None and clean_title != room.get("title"):
            room["title"] = clean_title
            _notice(room, f"title:{now}", f"{viewer} 把聊天室名称改为「{clean_title or '未命名'}」")
        if joining:
            room["members"] = [*room["members"], *joining]
            _notice(room, f"join:{now}", f"{viewer} 邀请 {'、'.join(joining)} 加入了聊天室")
        rooms[clean_id] = room
    _publish({**room, "members": [viewer]} if personal_only else room,
             fields=["preferences"] if personal_only else ["members", "messages"])
    return _public_room(room, viewer)


def leave_chatroom(actor: object, chatroom_id: object) -> dict[str, Any]:
    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    with _Transaction(clean_id, permissions=True) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        notified = dict(room)
        member = _member_id(room, viewer)
        room["members"] = [item for item in room["members"] if item != member]
        room.get("read_through", {}).pop(member.casefold(), None)
        room.get("muted_by", {}).pop(member.casefold(), None)
        room.get("user_mentions", {}).pop(member.casefold(), None)
        deleted = not room["members"]
        if deleted:
            rooms.pop(clean_id)
        else:
            _notice(room, f"leave:{member}:{_now()}", f"{member} 退出了聊天室")
            rooms[clean_id] = room
    if deleted:
        _agent_data().clear_chat(clean_id, actor_ref=viewer)
    _publish(notified, fields=["members", "messages"])
    return {"chatroom_id": clean_id, "deleted": deleted}


def delete_chatroom(actor: object, chatroom_id: object) -> dict[str, Any]:
    """The creator deletes the Chatroom for every member; its messages are archived."""

    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    with _Transaction(clean_id, permissions=True) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        rooms.pop(clean_id)
    _agent_data().clear_chat(clean_id, actor_ref=viewer)
    _publish(room, fields=["members", "messages"])
    return {"chatroom_id": clean_id, "deleted": True}


def _sync_permissions(room, *, deleted, connection):
    """Keep the Chatroom's place and its members' entries in the permission instance current, inside the room's transaction."""
    from management_portal import permission_system

    chatroom_id = room["chatroom_id"]
    if deleted:
        return permission_system.retire_resources([chatroom_id], connection=connection)
    return permission_system.sync_resource(_permission_record(room), resource_type="chatroom", connection=connection)


def forward_source(actor: object, chatroom_id: object, message_id: object) -> dict[str, str]:
    """One Chatroom message as ForwardMessages forwards it, read with the caller's own access."""

    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    room = _require_reader(_store().get(clean_id), viewer)
    clean_message = str(message_id or "").strip()
    stored = _agent_data().chat_message(clean_id, clean_message) if clean_message else None
    if stored is None:
        raise KeyError(f"消息不存在：{clean_message}")
    title = room.get("title") or "、".join(room.get("members") or [])
    return {
        "role": str(stored.get("role") or ""),
        "author": str(stored.get("author_id") or PORTAL_AUTHOR),
        "title": f"聊天室「{title}」",
        "content": "\n\n".join(
            str(part.get("text") or "")
            for part in stored.get("content_parts") or []
            if isinstance(part, Mapping) and part.get("type") == "text" and part.get("text")
        ),
    }


def mark_read(actor: object, chatroom_id: object, *, through: int | None = None) -> dict[str, Any]:
    if through is None:
        return set_unread(actor, chatroom_id, False)
    viewer, clean_id = _actor(actor), _chatroom_id(chatroom_id)
    with _Transaction(clean_id, viewer=viewer, delivery_ids=()) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        if isinstance(through, bool) or not isinstance(through, int) or not 0 <= through <= int(room.get("last_ordinal") or 0):
            raise ValueError("through 必须是这个聊天室已存在的消息位置")
        _mark_read(room, viewer, through)
        rooms[clean_id] = room
    _publish_inbox([viewer])
    return {"chatroom_id": clean_id, **_viewer_state(room, viewer)}


# -- messages ------------------------------------------------------------------------


def _project_messages(chatroom_id, viewer, messages):
    reactions = chatroom_store.reactions(chatroom_id, [m["message_id"] for m in messages])
    sources = {}
    for message in messages:
        source_id = str(message.get("reply_to_message_id") or (message.get("quote") or {}).get("message_id") or "")
        if source_id:
            if source_id not in sources:
                sources[source_id] = _agent_data().chat_message(chatroom_id, source_id)
            source = sources[source_id]
            # Existing user quotes are snapshots; retracting the source hides
            # their excerpt. Resident replies always link to the current source.
            if message.get("role") == "agent" or (source or {}).get("retracted_at"):
                quote = _message_quote(source)
                if quote:
                    message["quote"] = quote
        editable = message.get("role") == "user" and same_actor(message.get("author_id"), viewer) and not message.get("retracted_at")
        message["can_edit"] = bool(editable)
        message["can_retract"] = bool(editable)
        message["reactions"] = [
            {"emoji": emoji, "users": users, "count": len(users), "active": any(same_actor(user, viewer) for user in users)}
            for emoji, users in reactions.get(message["message_id"], {}).items()
        ] if not message.get("retracted_at") else []
    return messages


def list_messages(actor, chatroom_id, *, before="", after=0, limit=100, change_cursor=None):
    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    room = _require_reader(_store().get(clean_id), viewer)
    if _open_deliveries(room):
        reconcile_deliveries(clean_id)
    boundary = int(str(before).strip()) if str(before or "").strip() else None
    if change_cursor is not None and int(change_cursor or 0) < 0:
        raise ValueError("change_cursor 不能为负数")
    # The cursor and current versions are one snapshot: a mutation cannot be
    # acknowledged by the cursor before its content was read.
    with _store().transaction(clean_id):
        page = _agent_data().read_chat_message_page(clean_id, before_ordinal=boundary,
            after_ordinal=max(0, int(after or 0)), limit=max(1, min(MAX_PAGE_SIZE, int(limit or 100))))
        messages = [_public_message(m, n) for m, n in zip(page["messages"], page["ordinals"])]
        changed_ids, cursor = chatroom_store.message_changes(clean_id, change_cursor)
        changed = []
        for message_id in changed_ids:
            message = _agent_data().chat_message(clean_id, message_id)
            if message:
                changed.append(_public_message(message, _agent_data().chat_message_ordinal(clean_id, message_id)))
        _project_messages(clean_id, viewer, messages)
        _project_messages(clean_id, viewer, changed)
    next_before = page.get("next_before_ordinal")
    return {"chatroom_id": clean_id, "messages": messages, "next_before": str(next_before) if next_before else "",
            "updated_messages": changed, "change_cursor": cursor}


def input_history(actor, chatroom_id, *, before="", limit=50):
    """What the member posted here, newest first: the composer's input history.

    A retracted message comes back as it was before the retraction, so it can be posted again.
    """
    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    _require_reader(_store().get(clean_id), viewer)
    boundary = int(str(before).strip()) if str(before or "").strip() else None
    page = _agent_data().read_input_history(clean_id, viewer, before_ordinal=boundary,
        limit=max(1, min(MAX_PAGE_SIZE, int(limit or 50))))
    next_before = page.get("next_before_ordinal")
    return {"messages": [_public_message(m, n) for m, n in zip(page["messages"], page["ordinals"])],
            "next_before": str(next_before) if next_before else ""}


def search_messages(actor, chatroom_id, q, *, before="", limit=50):
    viewer = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    _require_reader(_store().get(clean_id), viewer)
    query = str(q or "").strip().casefold()
    if not query or len(query) > 200:
        raise ValueError("搜索关键词需要 1 到 200 个字符")
    size = max(1, min(100, int(limit or 50)))
    boundary = int(before) if before else None
    matches = []
    # Traverse current versions in bounded pages, independent of browser history.
    # Literal matching supports Chinese and punctuation without a second index.
    while True:
        page = _agent_data().read_chat_message_page(clean_id, before_ordinal=boundary, limit=MAX_PAGE_SIZE)
        for message, ordinal in reversed(list(zip(page["messages"], page["ordinals"]))):
            searchable = _message_text(message) + "\n" + "\n".join(str(p.get("name") or "") for p in message.get("content_parts", []) if p.get("type") in MEDIA_TYPES)
            if not message.get("retracted_at") and query in searchable.casefold():
                if len(matches) == size:
                    return {"messages": _project_messages(clean_id, viewer, matches), "next_before": str(matches[-1]["ordinal"])}
                matches.append(_public_message(message, ordinal))
        boundary = page.get("next_before_ordinal")
        if not boundary:
            return {"messages": _project_messages(clean_id, viewer, matches), "next_before": ""}


class MessageVersionConflict(ValueError):
    """The message changed since the caller read the version it expects."""

    def __init__(self, current_version: str):
        super().__init__("消息已被修改，请刷新后重试")
        self.current_version = current_version


def _owned_message(viewer, chatroom_id, message_id, expected_version):
    message = _trigger_message(chatroom_id, str(message_id or ""))
    if message.get("role") != "user" or not same_actor(message.get("author_id"), viewer):
        raise PermissionError("只能编辑或撤回自己发送的消息")
    if message.get("retracted_at"):
        raise ValueError("消息已经撤回")
    if expected_version is not None and message.get("segment_id") != expected_version:
        raise MessageVersionConflict(str(message.get("segment_id") or ""))
    return message


def _save_message_change(room, viewer, previous, current):
    room_id, message_id = room["chatroom_id"], previous["message_id"]
    # Pin pre-existing delivery intents before the first editable version is
    # created, including intents accepted before version pinning was introduced.
    for delivery in room.get("deliveries", []):
        if delivery.get("trigger_message_id") == message_id:
            delivery.setdefault("trigger_segment_id", previous["segment_id"])
    ordinal = _agent_data().chat_message_ordinal(room_id, message_id)
    stored = _agent_data().update_chat_message(room_id, message_id, current)
    # Edits remove obsolete mentions but do not send new notifications or tasks.
    prose = _mention_text(stored.get("content_parts") or [])
    for user, pending in room.get("user_mentions", {}).items():
        if stored.get("retracted_at") or not (_has_user_mention(prose, user) or _has_user_mention(prose, "全员")):
            room["user_mentions"][user] = [item for item in pending if item["message_id"] != message_id]
    if (room.get("last_message") or {}).get("message_id") == message_id:
        room["last_message"]["preview"] = _preview(stored)
        room["last_message"]["mentioned_users"] = [u for u in room["last_message"].get("mentioned_users", [])
            if not stored.get("retracted_at") and (_has_user_mention(prose, u) or _has_user_mention(prose, "全员"))]
    chatroom_store.record_message_change(room_id, message_id)
    return _project_messages(room_id, viewer, [_public_message(stored, ordinal)])[0]


def edit_message(actor, chatroom_id, message_id, content, *, expected_version):
    viewer, clean_id = _actor(actor), _chatroom_id(chatroom_id)
    if not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS:
        raise ValueError(f"消息正文最多 {MAX_MESSAGE_CHARS} 个字符")
    with _Transaction(clean_id) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        previous = _owned_message(viewer, clean_id, message_id, expected_version)
        media = [p for p in previous["content_parts"] if p.get("type") in MEDIA_TYPES]
        if not content.strip() and not media:
            raise ValueError("消息不能为空；如需移除请使用撤回")
        current = {**previous, "content_parts": ([{"type": "text", "text": content}] if content.strip() else []) + media, "edited_at": _now()}
        message = _save_message_change(room, viewer, previous, current)
        rooms[clean_id] = room
    _publish(room, fields=["messages"])
    return {"message": message}


def retract_message(actor, chatroom_id, message_id, *, expected_version):
    viewer, clean_id = _actor(actor), _chatroom_id(chatroom_id)
    with _Transaction(clean_id) as rooms:
        room = _require_member(rooms.get(clean_id), viewer)
        previous = _owned_message(viewer, clean_id, message_id, expected_version)
        current = {**previous, "content_parts": [], "retracted_at": _now()}
        current.pop("quote", None)
        chatroom_store.clear_reactions(clean_id, message_id)
        message = _save_message_change(room, viewer, previous, current)
        rooms[clean_id] = room
    _publish(room, fields=["messages"])
    return {"message": message}


def react_message(actor, chatroom_id, message_id, emoji, *, active):
    viewer, clean_id = _actor(actor), _chatroom_id(chatroom_id)
    if emoji not in REACTION_EMOJIS or not isinstance(active, bool):
        raise ValueError("请选择支持的表情并指定 active")
    with _store().transaction(clean_id):
        room = _require_member(_store().get(clean_id), viewer)
        message = _trigger_message(clean_id, str(message_id or ""))
        if message.get("retracted_at") or message.get("role") not in {"user", "agent"}:
            raise ValueError("这条消息不能添加表情回应")
        changed = chatroom_store.set_reaction(clean_id, message_id, emoji, viewer.casefold(), active)
        if changed:
            chatroom_store.record_message_change(clean_id, message_id)
        result = _project_messages(clean_id, viewer, [_public_message(message, _agent_data().chat_message_ordinal(clean_id, message_id))])[0]
    if changed:
        _publish(room, fields=["messages"], inbox=False)
    return {"message": result}


def _validated_parts(content_parts: object, chatroom_id: str, author: str) -> list[dict[str, Any]]:
    """Text and attachments, in the same parts a Resident message uses.

    An attachment is an upload its author sent to this Chatroom; its bytes are
    read back through the upload, so every reader of the Chatroom can open it.
    """

    from management_portal.resources.isa import read_upload

    if not isinstance(content_parts, list) or not content_parts:
        raise ValueError("消息不能为空")
    parts: list[dict[str, Any]] = []
    text_size = 0
    for raw in content_parts:
        if not isinstance(raw, Mapping):
            raise ValueError("content_parts 的每一项必须是对象")
        kind = str(raw.get("type") or "").strip()
        if kind == "text":
            text = str(raw.get("text") or "")
            if text.strip():
                text_size += len(text)
                parts.append({"type": "text", "text": text})
            continue
        if kind not in MEDIA_TYPES:
            raise ValueError(f"聊天室消息不支持 {kind or '未知'} 内容")
        upload_id = str(raw.get("upload_id") or "").strip()
        if not UPLOAD_ID_RE.fullmatch(upload_id):
            raise ValueError("附件必须先通过 CreateUpload 上传")
        meta = read_upload(upload_id)
        if not meta.get("ready"):
            raise ValueError("附件还没有上传完成")
        parts.append({
            "type": kind,
            "upload_id": upload_id,
            "name": str(meta.get("file_name") or raw.get("name") or kind),
            "mime_type": str(meta.get("media_type") or raw.get("mime_type") or "application/octet-stream"),
            "size": int(meta.get("size") or 0),
        })
    if not parts:
        raise ValueError("消息不能为空")
    if text_size > MAX_MESSAGE_CHARS:
        raise ValueError(f"消息最多 {MAX_MESSAGE_CHARS} 个字符")
    if sum(1 for part in parts if part["type"] in MEDIA_TYPES) > MAX_ATTACHMENTS:
        raise ValueError(f"一条消息最多 {MAX_ATTACHMENTS} 个附件")
    return parts


def _quote(chatroom_id: str, requested: object, message_id: str) -> dict[str, str]:
    """The quote a new message carries: it names one shown message of this Chatroom; the platform fills the rest.

    The same rules as a quote reply in a Resident Conversation
    (decision_c524c775e0be): only ``message_id`` is given, the author and the
    opening text are filled in once and never change.
    """

    if requested is None or requested == {}:
        return {}
    if not isinstance(requested, Mapping) or set(requested) != {"message_id"}:
        raise ValueError("quote 只给出被引用消息的 message_id，作者和摘录由平台填写")
    quoted_id = str(requested.get("message_id") or "").strip()
    if not quoted_id or quoted_id == message_id:
        raise ValueError("quote.message_id 需要指向这个聊天室中的另一条消息")
    quote = _message_quote(_agent_data().chat_message(chatroom_id, quoted_id))
    if not quote:
        raise ValueError(f"被引用的消息不在这个聊天室中，或没有正文：{quoted_id}")
    return quote


def post_message(
    actor: object,
    chatroom_id: object,
    content_parts: object,
    *,
    message_id: str = "",
    quote: object = None,
) -> dict[str, Any]:
    author = _actor(actor)
    clean_id = _chatroom_id(chatroom_id)
    parts = _validated_parts(content_parts, clean_id, author)
    with _Transaction(clean_id) as rooms:
        room = _require_member(rooms.get(clean_id), author)
        member = _member_id(room, author) or author
        new_id = message_id or derived_message_identity("chatroom-message", clean_id, member, _now(), secrets.token_hex(8))
        accepted = _quote(clean_id, quote, new_id)
        # Quoting a Resident is a reply to it, including through the ISA. Make
        # the invocation explicit in the stored text and the delivered message.
        quoted_resident = str(accepted.get("author_id") or "")
        if RESIDENT_ID_RE.fullmatch(quoted_resident) and quoted_resident not in MENTION_RE.findall(_mention_text(parts)):
            parts = [{"type": "text", "text": f"@{quoted_resident} "}, *parts]
            if len(_message_text({"content_parts": parts})) > MAX_MESSAGE_CHARS:
                raise ValueError("引用 Resident 后的消息超过字数上限")
        plain = _mention_text(parts)
        mention_all = _has_user_mention(plain, "全员")
        mentioned_users = [
            user for user in room["members"]
            if not same_actor(user, member) and (mention_all or _has_user_mention(plain, user))
        ]
        message = _append(room, {
            "message_id": new_id,
            "role": "user",
            "author_id": member,
            "content_parts": parts,
            **({"quote": accepted} if accepted else {}),
        })
        if message["ordinal"] == room["last_ordinal"]:
            room["last_message"]["mentioned_users"] = mentioned_users
        for user in mentioned_users:
            pending = room.setdefault("user_mentions", {}).setdefault(user.casefold(), [])
            if not any(item["message_id"] == message["message_id"] for item in pending):
                pending.append({"message_id": message["message_id"], "ordinal": message["ordinal"]})
        _mark_read(room, member, message["ordinal"])
        deliveries = _record_mentions(room, message, sender=member)
        rooms[clean_id] = room
        message = _project_messages(clean_id, author, [message])[0]
    _publish(room, fields=["messages"])
    results = [_deliver(clean_id, delivery_id) for delivery_id in deliveries]
    return {"message": message, "deliveries": [item for item in results if item]}


# -- @ delivery ------------------------------------------------------------------


def _mention_text(parts: list[dict[str, Any]]) -> str:
    """Only authored Markdown prose can mention people or call Residents."""
    from markdown_it import MarkdownIt

    texts = []
    quoted = 0
    for token in MarkdownIt("commonmark").parse(_message_text({"content_parts": parts})):
        if token.type == "blockquote_open":
            quoted += 1
        elif token.type == "blockquote_close":
            quoted -= 1
        elif token.type == "inline" and not quoted:
            link = 0
            for child in token.children or []:
                if child.type == "link_open":
                    link += 1
                elif child.type == "link_close":
                    link -= 1
                elif child.type == "text" and not link:
                    texts.append(child.content)
    return "\n".join(texts)


def _has_user_mention(text: str, username: str) -> bool:
    return bool(re.search(r"(?<![\w@.+-])@" + re.escape(username) + r"(?![\w@+-]|\.[\w])", text, flags=re.IGNORECASE))


def _record_mentions(room: dict[str, Any], message: Mapping[str, Any], *, sender: str) -> list[str]:
    """Record one delivery intent per mentioned Resident before anything is sent.

    A called Resident may read this Chatroom from then on, so it can look up the
    discussion it was called into.
    """

    mentioned = list(dict.fromkeys(MENTION_RE.findall(_mention_text(message.get("content_parts") or []))))[:MAX_MENTIONS]
    created = []
    for resident_id in mentioned:
        delivery_id = derived_message_identity("chatroom-delivery", room["chatroom_id"], message["message_id"], resident_id)
        if any(item.get("id") == delivery_id for item in room.get("deliveries") or []):
            continue
        now = _now()
        room.setdefault("deliveries", []).append({
            "id": delivery_id,
            "resident_id": resident_id,
            "trigger_message_id": message["message_id"],
            "trigger_segment_id": message["version"],
            "delivered_by": sender,
            "input_message_id": derived_message_identity("chatroom-input", room["chatroom_id"], message["message_id"], resident_id),
            "status": "queued",
            "error": "",
            "created_at": now,
            "updated_at": now,
        })
        if resident_id not in _called_residents(room):
            room["called_residents"] = [*_called_residents(room), resident_id]
        created.append(delivery_id)
    _prune_deliveries(room)
    return created


def _prune_deliveries(room: dict[str, Any]) -> None:
    deliveries = list(room.get("deliveries") or [])
    open_items = [item for item in deliveries if item.get("status") in OPEN_DELIVERY_STATES]
    closed = [item for item in deliveries if item.get("status") not in OPEN_DELIVERY_STATES]
    keep = max(0, DELIVERY_HISTORY_LIMIT - len(open_items))
    kept_closed = closed[-keep:] if keep else []
    room["deliveries"] = [item for item in deliveries if item in open_items or item in kept_closed]


def _open_deliveries(room: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in room.get("deliveries") or [] if item.get("status") in OPEN_DELIVERY_STATES]


def _execute(instruction: str, arguments: Mapping[str, Any], caller: str, *, idempotency_key: str = "") -> Any:
    """Run an instruction as the real sender; the ISA kernel checks that sender's credentials."""

    from management_portal.hatg import routing
    from management_portal.hatg.contracts import InstructionContext

    call = {"instruction": instruction, "arguments": dict(arguments)}
    if idempotency_key:
        call["idempotency_key"] = idempotency_key
    return routing.execute_or_raise(call, InstructionContext(caller=caller, entry="system"))


def _delivered_parts(room: Mapping[str, Any], trigger: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The calling message and where it came from; attachments travel as the same uploads."""

    title = room.get("title") or "、".join(room.get("members") or [])
    header = (
        f"{trigger.get('author_id') or '一位用户'} 在聊天室「{title}」（{room['chatroom_id']}）中 @ 了你，消息如下。"
        f"需要聊天上下文时，可以调用 HATG 指令 ListChatroomMessages（chatroom_id={room['chatroom_id']}）读取这个聊天室的记录。"
        "你对这条消息的回复会显示在聊天室中。"
    )
    body = _message_text(trigger)
    if trigger.get("quote"):
        # The Resident reads the quoted author and excerpt first, as in its own Conversation.
        from management_portal.society_service.message_quotes import with_quote

        body = with_quote(body, trigger["quote"])
    parts: list[dict[str, Any]] = [{"type": "text", "text": f"{header}\n\n{body}".rstrip()}]
    for part in trigger.get("content_parts") or []:
        if isinstance(part, Mapping) and part.get("type") in MEDIA_TYPES and part.get("upload_id"):
            parts.append({
                "type": part["type"],
                "media_type": part["type"],
                "upload_id": part["upload_id"],
                "mime_type": part.get("mime_type") or "",
                "name": part.get("name") or "",
            })
    return parts


def _trigger_message(chatroom_id: str, message_id: str, segment_id: str = "") -> dict[str, Any]:
    stored = _agent_data().chat_message(chatroom_id, message_id)
    if stored is None:
        raise KeyError(f"消息不存在：{message_id}")
    if segment_id and stored.get("segment_id") != segment_id:
        stored = next((m for m in _agent_data().chat_message_ancestors(stored["segment_id"], message_id) if m["segment_id"] == segment_id), None)
        if stored is None:
            raise ValueError("投递原始版本不存在")
    return dict(stored)


def _update_delivery(chatroom_id: str, delivery_id: str, change: Callable[[dict[str, Any], dict[str, Any]], None]) -> dict[str, Any] | None:
    with _Transaction(chatroom_id, delivery_ids=(delivery_id,)) as rooms:
        room = rooms.get(chatroom_id)
        if room is None:
            return None
        delivery = next((item for item in room.get("deliveries") or [] if item.get("id") == delivery_id), None)
        if delivery is None:
            return None
        change(room, delivery)
        delivery["updated_at"] = _now()
        rooms[chatroom_id] = room
        return dict(delivery)


def _deliver(chatroom_id: str, delivery_id: str) -> dict[str, Any] | None:
    """Send one queued delivery intent; replaying it delivers the same input at most once."""

    room = _store().get(chatroom_id, delivery_ids=(delivery_id,))
    delivery = next((item for item in (room or {}).get("deliveries") or [] if item.get("id") == delivery_id), None)
    if room is None or delivery is None or delivery.get("status") != "queued":
        return delivery
    try:
        trigger = _trigger_message(chatroom_id, delivery["trigger_message_id"], delivery.get("trigger_segment_id", ""))
        _execute(
            "DeliverMessage",
            {
                "resident_id": delivery["resident_id"],
                "message": {
                    "message_id": delivery["input_message_id"],
                    "role": "user",
                    "content_parts": _delivered_parts(room, trigger),
                },
                "activation_policy": "enqueue_and_trigger",
                "business_ref": f"{chatroom_id}/{delivery['trigger_message_id']}",
            },
            delivery["delivered_by"],
            idempotency_key=f"chatroom-delivery:{delivery_id}",
        )
    except Exception as error:  # noqa: BLE001 - the Chatroom reports why the Resident got nothing
        reason = str(getattr(error, "message", "") or error) or type(error).__name__

        def fail(target_room: dict[str, Any], target: dict[str, Any]) -> None:
            if target.get("status") != "queued":
                return
            target["status"] = "undeliverable"
            target["error"] = reason[:500]
            _notice(target_room, f"undeliverable:{delivery_id}", f"未能把消息交给 {target['resident_id']}：{reason[:300]}")

        updated = _update_delivery(chatroom_id, delivery_id, fail)
        if updated is not None:
            _publish(room, fields=["messages"])
        return updated

    def delivered(_room: dict[str, Any], target: dict[str, Any]) -> None:
        if target.get("status") == "queued":
            target["status"] = "delivered"

    updated = _update_delivery(chatroom_id, delivery_id, delivered)
    _publish(room, fields=["progress"], inbox=False)
    return updated


def _reply_messages(delivery: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    """Terminal status, shown replies and failure reason of one delivered input."""

    resident_id = str(delivery["resident_id"])
    caller = str(delivery["delivered_by"])
    state = _execute("GetInputState", {"resident_id": resident_id, "message_id": delivery["input_message_id"]}, caller)
    input_state = state.get("input_state") if isinstance(state, Mapping) and "input_state" in state else state
    input_state = input_state if isinstance(input_state, Mapping) else {}
    status = str(input_state.get("state") or "")
    if status not in {"completed", "failed", "cancelled"}:
        return status, [], ""
    replies = []
    if status == "completed":
        for reply_id in input_state.get("reply_message_ids") or []:
            result = _execute("GetMessage", {"resident_id": resident_id, "message_id": str(reply_id)}, caller)
            message = result.get("message") if isinstance(result, Mapping) else None
            if not isinstance(message, Mapping) or message.get("role") != "agent":
                continue
            parts = [
                dict(part) for part in message.get("content_parts") or []
                if isinstance(part, Mapping) and (
                    (part.get("type") == "text" and str(part.get("text") or "").strip())
                    or part.get("type") in MEDIA_TYPES
                )
            ]
            if parts:
                replies.append({**dict(message), "content_parts": parts})
    error = input_state.get("error") if isinstance(input_state.get("error"), Mapping) else {}
    return status, replies, str(error.get("message") or "")


def _project_terminal(chatroom_id: str, delivery: Mapping[str, Any]) -> None:
    """Show a finished delivery's reply in the Chatroom; it stays in the Resident's Conversation too."""

    status, replies, reason = _reply_messages(delivery)
    if status not in {"completed", "failed", "cancelled"}:
        return
    with _Transaction(chatroom_id, delivery_ids=(delivery["id"],)) as rooms:
        room = rooms.get(chatroom_id)
        if room is None:
            return
        target = next((item for item in room.get("deliveries") or [] if item.get("id") == delivery["id"]), None)
        if target is None or target.get("status") != "delivered":
            return
        resident_id = str(target["resident_id"])
        if status == "completed":
            for reply in replies:
                _append(room, {
                    "message_id": derived_message_identity("chatroom-reply", chatroom_id, str(reply["message_id"])),
                    "role": "agent",
                    "author_id": resident_id,
                    "content_parts": reply["content_parts"],
                    "reply_to_message_id": target["trigger_message_id"],
                })
            if not replies:
                _notice(room, f"empty-reply:{target['id']}", f"{resident_id} 已处理这条消息，没有回复内容。")
            target["status"] = "replied"
        else:
            target["status"] = status
            target["error"] = reason[:500]
            label = "失败" if status == "failed" else "已取消"
            _notice(room, f"terminal:{target['id']}", f"{resident_id} 未完成这次回复（{label}）。" + (f"原因：{reason[:300]}" if reason else ""))
        target["updated_at"] = _now()
        rooms[chatroom_id] = room
    _publish(room, fields=["messages"])


def _settled(delivery: Mapping[str, Any]) -> bool:
    try:
        created = datetime.fromisoformat(str(delivery.get("created_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - created).total_seconds() >= QUEUED_RETRY_AFTER_SECONDS


def reconcile_deliveries(chatroom_id: object) -> None:
    """Finish every open delivery of one Chatroom: send settled intents, bring back replies."""

    clean_id = _chatroom_id(chatroom_id)
    room = _store().get(clean_id)
    for delivery_id in [str(item["id"]) for item in _open_deliveries(room or {})]:
        current = next(
            (item for item in (_store().get(clean_id, delivery_ids=(delivery_id,)) or {}).get("deliveries") or [] if item.get("id") == delivery_id),
            None,
        )
        if current is None:
            continue
        try:
            if current.get("status") == "queued":
                if not _settled(current):
                    continue
                current = _deliver(clean_id, delivery_id) or current
            if current.get("status") == "delivered":
                _project_terminal(clean_id, current)
        except Exception:  # noqa: BLE001 - one unreadable delivery must not block the others
            LOGGER.warning("Chatroom %s delivery %s not reconciled", clean_id, delivery_id, exc_info=True)
            continue


def handle_resident_progress(*, business_ref: object, resident_id: object, message_id: object) -> dict[str, Any]:
    """SocietyService event hook: a called Resident's input moved in its queue or its live work changed.

    Only the Chatroom view wakes; it reads the progress again with ``GetChatroom``.
    """

    chatroom_id = str(business_ref or "").strip().lower().split("/", 1)[0]
    if not CHATROOM_ID_RE.fullmatch(chatroom_id):
        return {}
    room = _store().get(chatroom_id) or {}
    if not any(
        item.get("status") in OPEN_DELIVERY_STATES
        and item.get("resident_id") == str(resident_id or "") and item.get("input_message_id") == str(message_id or "")
        for item in room.get("deliveries") or []
    ):
        return {}
    _publish(room, fields=["progress"], inbox=False)
    return {"chatroom_id": chatroom_id}


def handle_resident_terminal(*, business_ref: object, resident_id: object, message_id: object) -> dict[str, Any]:
    """SocietyService terminal hook: bring the reply of a Chatroom delivery back now."""

    chatroom_id = str(business_ref or "").strip().lower().split("/", 1)[0]
    if not CHATROOM_ID_RE.fullmatch(chatroom_id):
        return {}
    room = _store().get(chatroom_id) or {}
    if not any(
        item.get("resident_id") == str(resident_id or "") and item.get("input_message_id") == str(message_id or "")
        for item in room.get("deliveries") or []
    ):
        return {}
    reconcile_deliveries(chatroom_id)
    return {"chatroom_id": chatroom_id}

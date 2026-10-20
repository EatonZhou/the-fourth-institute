"""Portal Web handlers of the Chatroom instructions (``web.instructions``).

The declarations are in ``chatroom_instructions.py``.  A chatroom message's
handler compares its own version with the call envelope's expected_version.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from management_portal.hatg.contracts import InstructionError
from management_portal.hatg.kernel import Invocation


def _chatrooms():
    from management_portal import chatroom_backend

    return chatroom_backend


def list_chatrooms(inv: Invocation) -> dict[str, Any]:
    from management_portal.web_isa import _named_user

    return {"chatrooms": _chatrooms().list_chatrooms(_named_user(inv))}


def get_chatroom(inv: Invocation) -> dict[str, Any]:
    return {"chatroom": _chatrooms().get_chatroom(inv.caller, inv.argument("chatroom_id"))}


def create_chatroom(inv: Invocation) -> dict[str, Any]:
    return {"chatroom": _chatrooms().create_chatroom(
        inv.caller, inv.argument("members") or [], inv.argument("title") or "",
    )}


def update_chatroom(inv: Invocation) -> dict[str, Any]:
    return {"chatroom": _chatrooms().update_chatroom(
        inv.caller, inv.argument("chatroom_id"),
        title=inv.argument("title"),
        add_members=inv.argument("add_members"),
        muted=inv.argument("muted"),
    )}


def leave_chatroom(inv: Invocation) -> dict[str, Any]:
    return _chatrooms().leave_chatroom(inv.caller, inv.argument("chatroom_id"))


def delete_chatroom(inv: Invocation) -> dict[str, Any]:
    return _chatrooms().delete_chatroom(inv.caller, inv.argument("chatroom_id"))


def list_chatroom_messages(inv: Invocation) -> dict[str, Any]:
    page = dict(_chatrooms().list_messages(
        inv.caller, inv.argument("chatroom_id"),
        before=inv.argument("cursor") or "",
        after=inv.argument("after") or 0,
        limit=inv.argument("limit") or 100,
        change_cursor=inv.argument("change_cursor"),
    ))
    older = page.pop("next_before", "")
    if older:
        page["next_cursor"] = older
    return page


def _chatroom_page(page: dict[str, Any]) -> dict[str, Any]:
    """A chatroom page in the ISA's paging names: next_before becomes next_cursor."""
    result = {"messages": page["messages"]}
    if page.get("next_before"):
        result["next_cursor"] = page["next_before"]
    return result


def list_chatroom_input_history(inv: Invocation) -> dict[str, Any]:
    """ListInputHistory with chatroom_id."""
    return _chatroom_page(_chatrooms().input_history(inv.caller, inv.argument("chatroom_id"),
        before=inv.argument("cursor") or "", limit=inv.argument("limit") or 50))


def search_chatroom_messages(inv: Invocation) -> dict[str, Any]:
    """SearchMessages with chatroom_id."""
    return _chatroom_page(_chatrooms().search_messages(inv.caller, inv.argument("chatroom_id"), inv.argument("query"),
        before=inv.argument("cursor") or "", limit=inv.argument("limit") or 50))


def _message_change(inv: Invocation, change: Callable[..., dict[str, Any]], *arguments: Any) -> dict[str, Any]:
    """An author's change to one message, compared with the envelope's expected_version when given."""
    from management_portal.chatroom_backend import MessageVersionConflict

    message_id = inv.argument("message_id")
    expected = inv.expected_version_for(message_id)
    try:
        return change(inv.caller, inv.argument("chatroom_id"), message_id, *arguments,
                      expected_version=None if expected is None else str(expected))
    except MessageVersionConflict as exc:
        raise InstructionError(
            "instruction.version_conflict", str(exc),
            details={"object_id": message_id, "expected_version": expected, "current_version": exc.current_version},
        ) from exc


def update_chatroom_message(inv: Invocation) -> dict[str, Any]:
    return _message_change(inv, _chatrooms().edit_message, inv.argument("content"))


def retract_chatroom_message(inv: Invocation) -> dict[str, Any]:
    return _message_change(inv, _chatrooms().retract_message)


def update_chatroom_reaction(inv: Invocation) -> dict[str, Any]:
    return _chatrooms().react_message(inv.caller, inv.argument("chatroom_id"), inv.argument("message_id"),
        inv.argument("emoji"), active=inv.argument("active"))


def create_chatroom_message(inv: Invocation) -> dict[str, Any]:
    message_id = ""
    if inv.idempotency_key:
        from management_common.message_identity import derived_message_identity

        message_id = derived_message_identity(
            "chatroom-post", inv.argument("chatroom_id"), inv.caller, inv.idempotency_key,
        )
    return _chatrooms().post_message(
        inv.caller, inv.argument("chatroom_id"), inv.argument("content_parts"), message_id=message_id,
        quote=inv.argument("quote"),
    )


def mark_chatroom_read(inv: Invocation) -> dict[str, Any]:
    return _chatrooms().mark_read(inv.caller, inv.argument("chatroom_id"), through=inv.argument("through"))


HANDLERS = {
    "ListChatrooms": list_chatrooms,
    "GetChatroom": get_chatroom,
    "CreateChatroom": create_chatroom,
    "UpdateChatroom": update_chatroom,
    "LeaveChatroom": leave_chatroom,
    "DeleteChatroom": delete_chatroom,
    "ListChatroomMessages": list_chatroom_messages,
    # The Web part of two core instructions: calls with chatroom_id run here (argument_owners).
    "SearchMessages": search_chatroom_messages,
    "ListInputHistory": list_chatroom_input_history,
    "UpdateChatroomMessage": update_chatroom_message,
    "RetractChatroomMessage": retract_chatroom_message,
    "UpdateChatroomReaction": update_chatroom_reaction,
    "CreateChatroomMessage": create_chatroom_message,
    "MarkChatroomRead": mark_chatroom_read,
}

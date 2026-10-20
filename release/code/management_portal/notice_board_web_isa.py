"""Portal Web handlers of the Notice Board instructions (``web.instructions``).

The declarations are in ``notice_board_instructions.py``; this module also
gives a post's version token.
"""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import invalid
from management_portal.hatg.kernel import Invocation


def _board():
    from management_portal import notice_board_backend

    return notice_board_backend.notice_board


def list_posts(inv: Invocation) -> dict[str, Any]:
    result = _board().list_posts(inv.caller)
    posts = list(result.get("posts") or [])
    category = inv.argument("category")
    if category:
        posts = [post for post in posts if post.get("category") == category]
    return {
        **result,
        "posts": posts,
        "post_count": len(posts),
        "featured_count": sum(1 for post in posts if post.get("featured")),
        "unread_count": sum(1 for post in posts if post.get("unread")),
    }


def get_post(inv: Invocation) -> dict[str, Any]:
    result = _board().get_post(inv.caller, inv.argument("post_id"))
    post = result.get("post") or {}
    return {
        **result,
        "post": post,
        "replies": list(result.get("replies") or post.get("replies") or []),
        "notification_revision": int(post.get("notification_revision") or result.get("notification_revision") or 0),
        "permissions": {
            "can_edit": bool(post.get("can_edit")), "can_delete": bool(post.get("can_delete")),
        },
    }


def create_post(inv: Invocation) -> dict[str, Any]:
    from management_portal.notice_board_classifier import enqueue_classification

    result = _board().create_post(inv.caller, inv.argument("title"), inv.argument("content"), inv.argument("category"))
    enqueue_classification((result.get("post") or {}).get("post_id"))
    return {"post": result.get("post") or {}}


def update_post(inv: Invocation) -> dict[str, Any]:
    changes = dict(inv.argument("changes") or {})
    unknown = sorted(set(changes) - {"title", "category", "content"})
    if unknown:
        raise invalid("changes 只能包含 title、category、content")
    current = _board().get_post(inv.caller, inv.argument("post_id")).get("post") or {}
    result = _board().update_post(
        inv.caller,
        inv.argument("post_id"),
        str(changes.get("title", current.get("title") or "")),
        str(changes.get("content", current.get("content") or "")),
        changes.get("category", current.get("category")),
    )
    return {"post": result.get("post") or {}}


def delete_post(inv: Invocation) -> dict[str, Any]:
    _board().delete_post(inv.caller, inv.argument("post_id"))
    return {"post_id": inv.argument("post_id")}


def create_reply(inv: Invocation) -> dict[str, Any]:
    result = _board().add_reply(inv.caller, inv.argument("post_id"), inv.argument("content"))
    post = result.get("post") or {}
    return {
        "reply": result.get("reply") or {},
        "notification_revision": int(post.get("notification_revision") or result.get("notification_revision") or 0),
    }


def delete_reply(inv: Invocation) -> dict[str, Any]:
    result = _board().delete_reply(inv.caller, inv.argument("post_id"), inv.argument("reply_id"))
    return {"post": result.get("post") or {}}


def pin_post(inv: Invocation) -> dict[str, Any]:
    return {"post": _board().set_pinned(inv.caller, inv.argument("post_id"), bool(inv.argument("pinned"))).get("post") or {}}


def feature_post(inv: Invocation) -> dict[str, Any]:
    return {"post": _board().set_featured(inv.caller, inv.argument("post_id"), bool(inv.argument("featured"))).get("post") or {}}


def mark_post_read(inv: Invocation) -> dict[str, Any]:
    board = _board()
    post_id = inv.argument("post_id")
    if inv.argument("unread"):
        result = board.mark_post_unread(inv.caller, post_id)
    else:
        revision = inv.argument("notification_revision")
        if revision is None:
            revision = int((board.get_post(inv.caller, post_id).get("post") or {}).get("notification_revision") or 1)
        result = board.mark_post_read(inv.caller, post_id, revision)
    return {
        "read_revision": int(result["read_revision"]),
        "unread_count": int(board.list_posts(inv.caller)["unread_count"]),
    }


HANDLERS = {
    "ListPosts": list_posts,
    "GetPost": get_post,
    "CreatePost": create_post,
    "UpdatePost": update_post,
    "DeletePost": delete_post,
    "CreateReply": create_reply,
    "DeleteReply": delete_reply,
    "PinPost": pin_post,
    "FeaturePost": feature_post,
    "MarkPostRead": mark_post_read,
}


def version_of(inv: Invocation, object_id: str) -> Any:
    """A post's version token is its notification revision (decision_898b8911a2ff “对象版本”)."""

    if object_id == inv.argument("post_id"):
        post = _board().get_post(inv.caller, object_id).get("post") or {}
        return int(post.get("notification_revision") or 0)
    return None

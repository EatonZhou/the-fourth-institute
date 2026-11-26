"""Portal Web handlers of the Notice Board instructions (``web.instructions``).

The declarations are in ``notice_board_instructions.py``; this module also
gives a post's version token.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from management_portal.hatg.contracts import InstructionError, invalid
from management_portal.hatg.kernel import Invocation


def _board():
    from management_portal import notice_board_backend

    return notice_board_backend.notice_board


def _expected(inv: Invocation) -> Any:
    """The notification revision the caller expects for the post this call targets."""
    return inv.expected_version_for(str(inv.argument("post_id") or ""))


@contextmanager
def _version_conflict(inv: Invocation) -> Iterator[None]:
    """The board compares the token in its write transaction; report a mismatch in the kernel's form."""
    from management_portal.notice_board_backend import PostVersionConflict

    try:
        yield
    except PostVersionConflict as exc:
        raise InstructionError(
            "instruction.version_conflict", str(exc),
            details={"object_id": exc.post_id, "expected_version": exc.expected_version,
                     "current_version": exc.current_version},
        ) from exc


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
    with _version_conflict(inv):
        result = _board().update_post(
            inv.caller,
            inv.argument("post_id"),
            str(changes.get("title", current.get("title") or "")),
            str(changes.get("content", current.get("content") or "")),
            changes.get("category", current.get("category")),
            expected_revision=_expected(inv),
        )
    return {"post": result.get("post") or {}}


def delete_post(inv: Invocation) -> dict[str, Any]:
    with _version_conflict(inv):
        _board().delete_post(inv.caller, inv.argument("post_id"), expected_revision=_expected(inv))
    return {"post_id": inv.argument("post_id")}


def create_reply(inv: Invocation) -> dict[str, Any]:
    with _version_conflict(inv):
        result = _board().add_reply(inv.caller, inv.argument("post_id"), inv.argument("content"),
                                    expected_revision=_expected(inv))
    post = result.get("post") or {}
    return {
        "reply": result.get("reply") or {},
        "notification_revision": int(post.get("notification_revision") or result.get("notification_revision") or 0),
    }


def delete_reply(inv: Invocation) -> dict[str, Any]:
    with _version_conflict(inv):
        result = _board().delete_reply(inv.caller, inv.argument("post_id"), inv.argument("reply_id"),
                                       expected_revision=_expected(inv))
    return {"post": result.get("post") or {}}


def update_post_marks(inv: Invocation) -> dict[str, Any]:
    with _version_conflict(inv):
        return _board().update_marks(
            inv.caller, inv.argument("post_id"),
            pinned=inv.argument("pinned"), featured=inv.argument("featured"),
            expected_revision=_expected(inv),
        )


def mark_post_read(inv: Invocation) -> dict[str, Any]:
    board = _board()
    post_id = inv.argument("post_id")
    revision = inv.argument("notification_revision")
    if revision is None:
        revision = int((board.get_post(inv.caller, post_id).get("post") or {}).get("notification_revision") or 1)
    with _version_conflict(inv):
        result = board.mark_post_read(inv.caller, post_id, revision, expected_revision=_expected(inv))
    return _read_receipt(board, inv.caller, result)


def set_post_unread(inv: Invocation) -> dict[str, Any]:
    board = _board()
    with _version_conflict(inv):
        result = board.mark_post_unread(inv.caller, inv.argument("post_id"), expected_revision=_expected(inv))
    return _read_receipt(board, inv.caller, result)


def _read_receipt(board, actor, result):
    return {
        "read_revision": int(result["read_revision"]),
        "unread_count": int(board.list_posts(actor)["unread_count"]),
    }


HANDLERS = {
    "ListPosts": list_posts,
    "GetPost": get_post,
    "CreatePost": create_post,
    "UpdatePost": update_post,
    "DeletePost": delete_post,
    "CreateReply": create_reply,
    "DeleteReply": delete_reply,
    "UpdatePostMarks": update_post_marks,
    "UpdatePostReadVersion": mark_post_read,
    "SetPostUnread": set_post_unread,
}


def version_of(inv: Invocation, object_id: str) -> Any:
    """A post's version token is its notification revision (decision_898b8911a2ff “对象版本”)."""

    if object_id == inv.argument("post_id"):
        post = _board().get_post(inv.caller, object_id).get("post") or {}
        return int(post.get("notification_revision") or 0)
    return None

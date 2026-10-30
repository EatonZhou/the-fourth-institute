"""The viewer's Inbox as Portal Web serves it: the conversation index page with the
features' Mailbox items, and marking, pinning and clearing conversations.
"""
from __future__ import annotations

import re

from management_portal import (
    agent_conversation_index_backend,
    conversation_read_state,
    mailbox_sources,
    web_clients,
    web_read_caches,
)
from management_portal.http_timing import timed_call
from management_portal.notifications import publish_projection_change


def _cached_agent_conversation_payload(
    actor: str,
    *,
    resident_id: str = "",
    page_size: int = 50,
    page_token: str = "",
) -> dict:
    # Event-driven targets must not spend their retry budget waiting for a
    # previously cached projection to expire. Browsers coalesce these reads.
    if resident_id:
        return _agent_conversation_payload(actor, resident_id=resident_id)
    key = (str(actor or ""), str(resident_id or ""), int(page_size), str(page_token or ""))
    return web_read_caches._AGENT_CONVERSATION_READ_CACHE.get(
        key,
        lambda: _agent_conversation_payload(
            actor,
            resident_id=resident_id,
            page_size=page_size,
            page_token=page_token,
        ),
        allow_stale=False,
    )


def _mailbox_source_entries(actor: str) -> list[dict]:
    """The features' Mailbox items: Notice Board mentions, Chatrooms (``mailbox_sources``)."""
    return mailbox_sources.entries(actor)


def _agent_conversation_entries(
    actor: str,
    resident_ids: list[str] | None = None,
) -> list[dict]:
    resident_entries = agent_conversation_index_backend.build_entries(
        actor,
        resident_ids=resident_ids,
    )
    other_entries = _mailbox_source_entries(actor) if resident_ids is None else []
    return agent_conversation_index_backend.normalized_entries(
        [*resident_entries, *other_entries]
    )


def _agent_conversation_payload(
    actor: str,
    *,
    resident_id: str = "",
    page_size: int = 50,
    page_token: str = "",
) -> dict:
    actor_id = str(actor or "").strip()
    read_state = timed_call("portal_conv_read_state", conversation_read_state.load, actor_id)
    target_ids = [resident_id] if resident_id else None
    target_state = ""
    resident_cursor = 0
    if resident_id:
        try:
            timed_call("portal_conv_identity", web_clients.resident_api.resolve_resident_identity, resident_id, actor_id)
            target_state = "present"
            resident_cursor = timed_call("portal_conv_cursor", agent_conversation_index_backend.canonical_resident_cursor,
                actor_id,
                resident_id,
            )
        except KeyError:
            target_state = "deleted"
        except PermissionError:
            target_state = "forbidden"
    page = (
        timed_call("portal_conv_entries", agent_conversation_index_backend.build_entries_page,
            actor_id, page_size=page_size, page_token=page_token,
        )
        if not resident_id else {}
    )
    if page and not page_token:
        # The features' items (Notice Board mentions, Chatrooms) are not Resident
        # activity pages; the first page carries all of them so each reaches the Inbox once.
        page["entries"] = agent_conversation_index_backend.normalized_entries(
            [*page["entries"], *timed_call("portal_conv_entries", _mailbox_source_entries, actor_id)]
        )
        pinned_keys = set(read_state.get("pinned_keys") or [])
        if pinned_keys:
            # This request already authorized and assembled the page's entries.
            # Only pins outside that snapshot need their own source read.
            page_keys = {entry["key"] for entry in page["entries"]}
            pinned_residents = [key.removeprefix("resident:") for key in pinned_keys
                                if key.startswith("resident:") and key not in page_keys]
            pinned_entries = timed_call("portal_conv_entries", _agent_conversation_entries, actor_id, pinned_residents) if pinned_residents else []
            page["entries"] = agent_conversation_index_backend.normalized_entries([*page["entries"], *pinned_entries])
            available_keys = {entry["key"] for entry in page["entries"]}
            read_state = timed_call("portal_conv_read_state", conversation_read_state.retain_pins, actor_id, available_keys)
    elif page and page_token:
        # Pinned targets were already returned on page one, even when old.
        pinned_keys = set(read_state.get("pinned_keys") or [])
        page["entries"] = [entry for entry in page["entries"] if entry["key"] not in pinned_keys]
    entries = page["entries"] if page else (
        timed_call("portal_conv_entries", _agent_conversation_entries, actor_id, target_ids)
        if target_state == "present" else []
    )
    payload = timed_call("portal_conv_decorate", agent_conversation_index_backend.decorate_entries,
        entries,
        read_state,
    )
    if page:
        payload.update({key: value for key, value in page.items() if key != "entries"})
    if resident_id:
        target_entries = [
            entry
            for entry in entries
            if str(entry.get("resident_id") or "") == resident_id
        ]
        payload["resident_id"] = resident_id
        payload["target_state"] = target_state
        payload["resident_cursor"] = resident_cursor
        payload["projection_cursor"] = max(
            (int(entry.get("cursor") or 0) for entry in target_entries),
            default=0,
        )
        payload["target_present"] = bool(target_entries)
    if not resident_id and not page_token:
        # Use the same viewer receipts and head entries for rows and badges.
        # Older pages contribute only unread markers; never hydrate their states.
        summary = {item["key"]: item for item in timed_call("portal_conv_summary", agent_conversation_index_backend.unread_resident_entries, actor_id, read_state)}
        for item in payload["entries"]:
            summary[item["key"]] = item
        payload["mailbox_summary"] = timed_call("portal_conv_summary", agent_conversation_index_backend.mailbox_summary, list(summary.values()))
        payload["unread_count"] = payload["mailbox_summary"]["unread_count"]
    payload.update({"ready": True, "refreshing": False, "error": ""})
    return payload


def _conversation_item(key: str, state: dict) -> dict:
    """What a browser keeps of a conversation's row: it holds the highest revision."""
    return {"key": key, "unread": state["unread"], "pinned": state["pinned"],
            "read_revision": state["read_revision"], "dismissed_through": state["dismissed_through"]}


def _set_agent_conversation_read_state(
    actor: str,
    key: str,
    *,
    unread: bool,
    expected_revision: object = None,
) -> dict:
    """Mark one conversation read or unread; ``expected_revision`` refuses a change seen out of date."""
    actor_id = str(actor or "").strip()
    key = str(key or "").strip()
    if not actor_id or not key:
        raise PermissionError("需要登录后修改对话状态")
    if len(key) > 512:
        raise ValueError("invalid conversation key")
    source = mailbox_sources.source_of(key)
    if source is not None and source.set_unread is not None:
        # A Chatroom moves its members' read positions itself, in their rows.
        item = source.set_unread(actor_id, key.removeprefix(source.prefix), unread,
                                 expected_revision=expected_revision)
        web_read_caches._invalidate_agent_conversation_cache()
        return {"key": key, "unread": item["unread"], "read_revision": item["read_revision"]}
    state = conversation_read_state.set_read(
        actor_id, key, unread_value=unread, expected_revision=expected_revision,
    )
    web_read_caches._forget_cached_inbox(actor_id)
    web_read_caches._invalidate_agent_conversation_cache()
    _publish_conversation_read_changes(actor_id, {key})
    return _conversation_item(key, state)


def _publish_conversation_read_changes(actor: str, keys: set[str], *, bulk: bool = False) -> None:
    if not keys and not bulk:
        return
    publish_projection_change("agent-conversation-index", "current", actor=actor, fields=["unread"], details={"keys": sorted(keys)})
    if bulk:
        publish_projection_change("resident-view-state", "current", actor=actor, fields=["unread"])
    else:
        for key in keys:
            if re.fullmatch(r"resident:resident_[0-9a-f]{12}", key):
                publish_projection_change("resident-view-state", key.removeprefix("resident:"),
                                                actor=actor, fields=["unread"])


def _clear_agent_conversations(actor: str, *, mode: str, entries: list[dict]) -> dict:
    """Read the listed unread conversations, or read and hide every listed unpinned one.

    The server lists ``entries`` at the request: a hidden conversation returns
    once its activity passes the ``updated_at`` it had then.
    """
    actor_id = str(actor or "").strip()
    clean_mode = str(mode or "").strip().lower()
    if not actor_id:
        raise PermissionError("需要登录后清理对话")
    if clean_mode not in {"unread", "all"}:
        raise ValueError("mode must be unread or all")
    # Chatrooms keep their members' read positions themselves; hiding them
    # until their next message works like any other conversation.
    own_read_state = mailbox_sources.own_read_state_prefixes()
    rooms = [entry for entry in entries if str(entry.get("key") or "").startswith(own_read_state) and entry.get("unread")]
    for entry in rooms:
        source = mailbox_sources.source_of(str(entry["key"]))
        source.set_unread(actor_id, str(entry["key"]).removeprefix(source.prefix), False)
    if clean_mode == "unread":
        changed = conversation_read_state.read_through(actor_id, [
            str(entry["key"]) for entry in entries
            if entry.get("unread") and not str(entry["key"]).startswith(own_read_state)
        ])
        cleared_count = len(changed) + len(rooms)
    else:
        changed = conversation_read_state.dismiss(actor_id, {
            str(entry["key"]): entry.get("updated_at") or "" for entry in entries if not entry.get("pinned")
        })
        cleared_count = len(changed)
    web_read_caches._forget_cached_inbox(actor_id)
    web_read_caches._invalidate_agent_conversation_cache()
    _publish_conversation_read_changes(actor_id, set(changed), bulk=True)
    return {"clear_mode": clean_mode, "cleared_count": cleared_count,
            "items": [_conversation_item(key, state) for key, state in changed.items()]}

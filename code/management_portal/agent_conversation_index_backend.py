from __future__ import annotations

import hashlib
import json
from typing import Any

from management_portal.society_service import get_society_service
from management_portal import conversation_index_projection
from management_portal import conversation_read_state
from management_portal import mailbox_sources
from management_portal.conversation_completion import marker_order


SCHEMA = "tfi.portal.agent-conversation-index.v1"


def section_of(entry: dict[str, Any]) -> str:
    """The index owns whether opening an item leads to a replyable Chat."""
    conversation = entry.get("kind") in {"resident-main", "resident"} or str(entry.get("key") or "").startswith(
        mailbox_sources.conversation_prefixes())
    return "conversation" if conversation else "notification"


def section_of_key(key: str) -> str:
    return "conversation" if str(key).startswith(("resident:", *mailbox_sources.conversation_prefixes())) else "notification"


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "unread_count": sum(bool(entry.get("attention_unread", entry.get("unread"))) for entry in entries),
        "sections": {
            section: {"count": sum(section_of(entry) == section for entry in entries),
                      "unread_count": sum(section_of(entry) == section and bool(entry.get("attention_unread", entry.get("unread"))) for entry in entries)}
            for section in ("conversation", "notification")
        },
    }


def canonical_resident_cursor(actor: str, resident_id: str) -> int:
    """Read the event cursor from the canonical Resident state owner."""

    snapshot = get_society_service().resident.get_resident_state(
        str(resident_id or "").strip(),
        str(actor or "").strip(),
    )
    return max(0, int(snapshot.get("cursor") or 0))


def normalized_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_by_key: dict[str, dict[str, Any]] = {}
    for raw_entry in entries:
        key = str(raw_entry.get("key") or "")
        if not key:
            continue
        entry = dict(raw_entry)
        entry["section"] = section_of(entry)
        # The time stays the one the list was paged by; it already counts the completion.
        if entry.get("completion_signature") and not marker_order(entry["completion_signature"])[2]:
            entry["completion_signature"] = ""
        current = normalized_by_key.get(key)
        if current is None or (
            max(0, int(entry.get("cursor") or 0)),
            conversation_index_projection.activity_timestamp(entry.get("updated_at")),
        ) >= (
            max(0, int(current.get("cursor") or 0)),
            conversation_index_projection.activity_timestamp(current.get("updated_at")),
        ):
            normalized_by_key[key] = entry
    return newest_first(list(normalized_by_key.values()))


def newest_first(entries: list[dict[str, Any]], *, pinned_first: bool = False) -> list[dict[str, Any]]:
    """Newest activity first; equal times by key ascending, as the activity pages order them."""
    entries.sort(key=lambda item: str(item.get("key") or ""))
    entries.sort(key=lambda item: (
        bool(item.get("pinned")) if pinned_first else False, conversation_index_projection.activity_timestamp(item.get("updated_at")),
    ), reverse=True)
    return entries


def build_entries(
    actor: str,
    *,
    resident_payload: object = None,
    resident_ids: list[str] | None = None,
    review_items: object = None,
) -> list[dict[str, Any]]:
    del review_items
    actor_id = str(actor or "").strip()
    if not actor_id:
        return []
    entries = conversation_index_projection.collect_entries(
        actor=actor_id,
        resident_payload=resident_payload,
        resident_ids=resident_ids,
        lineage_ids=[] if resident_ids is not None else None,
    )
    # A user can pin a node from its Menu before talking to it. Pins are viewer
    # state, not synthetic messages or read receipts in the conversation owner.
    present = {item.get("key") for item in entries}
    requested = set(resident_ids) if resident_ids is not None else None
    for key in conversation_read_state.load(actor_id).get("pinned_keys") or []:
        if not key.startswith("resident:") or key in present:
            continue
        resident_id = key.removeprefix("resident:")
        if requested is not None and resident_id not in requested:
            continue
        try:
            entries.append(conversation_index_projection.resident_pin_entry(actor_id, resident_id))
        except (KeyError, PermissionError):
            # Confirmed deletion or lost access releases the pin on the head read.
            continue
    return normalized_entries(
        [dict(item) for item in entries or [] if isinstance(item, dict)]
    )


def build_entries_page(
    actor: str, *, page_size: int = 50, page_token: str = "",
) -> dict[str, Any]:
    resident_payload = conversation_index_projection.get_society_service().resident.list_resident_conversation_activity(
        actor, page_size=page_size, page_token=page_token,
    )
    entries = conversation_index_projection.collect_entries(
        actor=actor,
        resident_payload=resident_payload,
        lineage_ids=[] if page_token else None,
    )
    return {
        "entries": normalized_entries(entries),
        "next_page_token": str(resident_payload.get("next_page_token") or ""),
        "page_size": page_size,
        "paged": True,
    }


def unread_resident_entries(actor: str, read_state: dict[str, Any]) -> list[dict[str, Any]]:
    """The viewer's unread Resident rows; activity pages and transcripts are not unread state."""
    candidates = {
        key.removeprefix("resident:"): conversation_read_state.row(read_state, key)
        for key in conversation_read_state.unread_keys(read_state) if key.startswith("resident:")
    }
    if not candidates:
        return []
    # Visibility belongs to SocietyService. Its internal domain ports are not
    # configured in the Web process; use the supported batch read contract.
    visible = get_society_service().resident.filter_visible_resident_ids(
        list(candidates), actor, "portal",
    )["resident_ids"]
    return [{
        "key": "resident:" + resident_id, "resident_id": resident_id,
        "kind": "resident-main", "section": "conversation", "unread": True,
        "read_revision": candidates[resident_id]["read_revision"],
    } for resident_id in visible]


def mailbox_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """One compact membership projection from which all badge totals derive."""
    fields = ("key", "kind", "section", "resident_id", "read_revision",
              "unread", "attention_unread", "pinned", "cursor")
    return {
        **summarize_entries(entries),
        "entries": [{key: item[key] for key in fields if key in item} for item in entries
                    if item.get("attention_unread", item.get("unread"))],
    }


def decorate_entries(
    entries: list[dict[str, Any]],
    read_state: object,
) -> dict[str, Any]:
    public_entries: list[dict[str, Any]] = []
    own_read_state = mailbox_sources.own_read_state_prefixes()
    for raw in entries:
        entry = dict(raw)
        row = conversation_read_state.row(read_state, str(entry.get("key") or ""))
        entry["section"] = section_of(entry)
        entry["pinned"] = entry["section"] == "conversation" and row["pinned"]
        # "清除全部" hides a conversation until its activity passes the clear.
        if not entry["pinned"] and row["dismissed_through"] and (
            conversation_index_projection.activity_timestamp(entry.get("updated_at")) <= conversation_index_projection.activity_timestamp(row["dismissed_through"])
        ):
            continue
        # An item whose feature keeps its read state (a Chatroom counts it from
        # its member rows, decision_a6bebf4ac283) carries its own unread.
        own = str(entry.get("key") or "").startswith(own_read_state)
        entry["unread"] = bool(raw.get("unread")) if own else row["unread"]
        entry["read_revision"] = row["read_revision"]
        entry["dismissed_through"] = row["dismissed_through"]
        public_entries.append(entry)
    newest_first(public_entries, pinned_first=True)
    signature_payload = [
        {
            key: entry.get(key)
            for key in (
                "key",
                "title",
                "subtitle",
                "surface_label",
                "url",
                "updated_at",
                "status",
                "intake_state",
                "account_id",
                "sprite_index",
                "sprite_rarity",
                "avatar_url",
                "completion_signature",
                "unread", "read_revision",
                "section", "pinned",
                "unread_count", "muted", "attention_unread", "unread_mention_count", "unread_mentions",
            )
        }
        for entry in public_entries
    ]
    content_signature = hashlib.sha256(
        json.dumps(
            signature_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    rows = read_state.get("rows") if isinstance(read_state, dict) else None
    return {
        "schema": SCHEMA,
        "entries": public_entries,
        **summarize_entries(public_entries),
        "content_signature": content_signature,
        # The newest row this snapshot saw: a browser keeps a later change of
        # a conversation the summary leaves out (portal_conversation_store.js).
        "read_revision": max((row["read_revision"] for row in (rows or {}).values()), default=0),
    }

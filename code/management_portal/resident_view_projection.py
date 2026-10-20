"""Actor-scoped unread flags carried by Resident state snapshots."""
from __future__ import annotations

from management_portal import conversation_read_state


def attach_viewer_state(items: list[dict], actor: str) -> list[dict]:
    if not items:
        return items
    rows = conversation_read_state.read_rows(actor, ["resident:" + item["resident_id"] for item in items])
    for item in items:
        row = rows.get("resident:" + item["resident_id"]) or {}
        item["viewer_state"] = {"unread": bool(row.get("unread")), "read_revision": int(row.get("read_revision") or 0)}
    return items

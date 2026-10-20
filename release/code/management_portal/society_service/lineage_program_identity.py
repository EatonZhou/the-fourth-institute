"""Stable identities for Program deliveries and event runs."""

from __future__ import annotations

import hashlib

from . import lineage_primitives


def delivery_message_id(
    lineage_id: str, event_id: str, source_wire_id: str, target_id: str,
    *, packet_id: str = "",
) -> str:
    identity = (lineage_id, event_id, source_wire_id, target_id)
    if packet_id:
        identity += (packet_id,)
    digest = hashlib.sha256(
        "\x1f".join(identity).encode("utf-8")
    ).hexdigest()
    return f"msg_{digest[:32]}"


def trigger_path_id(lineage_id: str, event_id: str) -> str:
    from .trigger_path_storage import path_id
    return path_id(lineage_id, "event", event_id)


def event_path_id(lineage_id: str, event: lineage_primitives.LineageEvent) -> str:
    return str(event.payload.get("trigger_path_id") or "") or trigger_path_id(lineage_id, event.id)

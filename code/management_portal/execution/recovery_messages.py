"""The input a Harness gives its Provider on its own, as a Chat message.

When a turn is recovered (a Worker handover, a Provider process that no
longer ran the turn, a Provider error or transition), the Harness sends the
Provider an input nobody typed: ``continue`` when the Provider had the turn's
input, or the turn's input itself when it never had it (``recovery_input_sent``,
see ``backends.report_recovery_input``).  That input is recorded like any other
message: in the Resident's trace where it was sent, and so in its Chat, as a
message of the Agent Harness.

The ExecutionService puts it in the trace as it goes out; Portal announces it
to the Conversation from the Execution's events.  Both build it from the
event, which carries the message's identity and time.
"""
from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from management_common.message_identity import derived_message_identity

RECOVERY_INPUT_EVENT = "recovery_input_sent"
HARNESS_AUTHOR = "agent_harness"
HARNESS_INPUT_FIELDS = ("provider", "reason", "input_delivery", "turn_id", "image_count")


def stamp_recovery_input(execution_id: str, details: Mapping[str, Any]) -> dict[str, Any]:
    """The event as it is recorded: with the identity and time of its message."""

    stamped = dict(details)
    stamped.setdefault("message_id", derived_message_identity(
        "harness-recovery-input", execution_id or "unknown", secrets.token_hex(8),
    ))
    stamped.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    return stamped


def recovery_input_message(details: Mapping[str, Any], *, execution_id: str) -> dict[str, Any] | None:
    """The Chat message of one recovery input; None for an event without its identity or text."""

    message_id = str(details.get("message_id") or "").strip()
    prompt = str(details.get("prompt") or "")
    if not message_id or not prompt.strip():
        return None
    harness_input = {
        key: details[key] for key in HARNESS_INPUT_FIELDS
        if details.get(key) not in (None, "", 0)
    }
    message: dict[str, Any] = {
        "message_id": message_id,
        "role": "system",
        "author_id": HARNESS_AUTHOR,
        "content_parts": [{"type": "text", "text": prompt}],
        "created_at": str(details.get("created_at") or ""),
        "harness_input": harness_input,
    }
    if execution_id:
        message["execution_id"] = execution_id
    if details.get("turn_id"):
        message["provider_turn_id"] = str(details["turn_id"])
    return message

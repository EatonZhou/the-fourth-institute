from __future__ import annotations

from management_common.operation_errors import OperationError


import re
import secrets
from collections.abc import Mapping

from management_common.message_identity import derived_message_identity  # noqa: F401 - shared message identity


MESSAGE_IDENTITY_PATTERN = re.compile(r"msg_[0-9a-f]{16,64}")
# ``quote`` names only the quoted message; the platform fills in its author and text.
DELIVER_MESSAGE_FIELDS = frozenset({"message_id", "role", "content_parts", "quote"})
ACTIVATION_POLICIES = ("enqueue_and_trigger", "inject_active_turn", "attach_to_next")


def validate_content_parts(parts: object) -> None:
    """Validate the shared Message part container; content owners decode parts."""
    if not isinstance(parts, list):
        raise ValueError("message.content_parts must be an array")
    if any(not isinstance(part, Mapping) for part in parts):
        raise ValueError("message.content_parts items must be objects")


def validate_delivery_message(message: object) -> None:
    """Validate the authored envelope without requiring a nonempty prompt.

    Effect composition may supply text later. Actual delivery still requires
    its text part and validates identity, quote and content at their owners.
    """
    if not isinstance(message, Mapping):
        raise ValueError("message must be an object")
    unexpected = sorted(set(message) - DELIVER_MESSAGE_FIELDS)
    if unexpected:
        raise ValueError("DeliverMessage message received unsupported fields: " + ", ".join(unexpected))
    if str(message.get("role") or "user").strip().casefold() != "user":
        raise ValueError("DeliverMessage accepts user messages only")
    if "content_parts" in message:
        validate_content_parts(message["content_parts"])


class MessageIdentityConflict(OperationError, ValueError):
    """A message ID already belongs to different immutable content."""

    code = "instruction.conflict"
    http_status = 409
    commit_state = "not_committed"


def activation_policy(value: object = None) -> str:
    policy = str(value or "enqueue_and_trigger").strip().lower()
    if policy not in ACTIVATION_POLICIES:
        raise ValueError(
            "activation_policy must be enqueue_and_trigger, inject_active_turn or attach_to_next"
        )
    return policy


def _string(value: object) -> str:
    return str(value or "").strip()


def message_identity(value: object = "") -> str:
    identity = _string(value)
    if identity and not MESSAGE_IDENTITY_PATTERN.fullmatch(identity):
        raise ValueError("invalid MessageIdentity")
    return identity or f"msg_{secrets.token_hex(16)}"


def delivered_message_id(response: object) -> str:
    """Read only the identity acknowledged for this delivery, never a projection tail."""
    if not isinstance(response, Mapping):
        return ""
    direct = _string(response.get("message_id"))
    message = response.get("message")
    nested = _string(message.get("message_id")) if isinstance(message, Mapping) else ""
    if direct and nested and direct != nested:
        raise ValueError("Resident delivery acknowledgement has conflicting message IDs")
    return direct or nested

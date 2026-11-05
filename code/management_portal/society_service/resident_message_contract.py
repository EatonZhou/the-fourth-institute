from __future__ import annotations

from management_common.operation_errors import OperationError


import re
import secrets
from collections.abc import Mapping
from typing import Any

from management_common.message_identity import derived_message_identity  # noqa: F401 - shared message identity
from management_common.text_values import stripped_text


MESSAGE_IDENTITY_PATTERN = re.compile(r"msg_[0-9a-f]{16,64}")


def input_parts(
    message: Mapping[str, Any],
) -> tuple[
    str,
    list[dict[str, Any]],
    dict[str, Any],
    str,
    list[str],
    str,
]:
    parts = message.get("content_parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("message.content_parts required")
    validate_content_parts(parts)
    text_parts: list[str] = []
    media: list[dict[str, Any]] = []
    context_refs: dict[str, Any] = {}
    context_text: list[str] = []
    instruction_skills: list[str] = []
    working_directory = ""
    for raw in parts:
        part = dict(raw)
        part_type = str(part.get("type") or "").strip().casefold()
        if part_type == "text":
            value = str(part.get("text") or "")
            text_parts.append(value)
        elif part_type in {"image", "file"}:
            media.append(part)
        elif part_type == "turn_review":
            if not isinstance(part.get("segment_id"), str) or not part["segment_id"]:
                raise ValueError("turn review requires a source Segment")
            text_parts.append("审查指定轮次的修改快照")
        elif part_type in {
            "artifact_reference",
            "resource_reference",
            "context_reference",
        }:
            if (
                part_type == "context_reference"
                and isinstance(part.get("context"), Mapping)
            ):
                context = dict(part["context"])
                raw_context_text = str(
                    context.pop("context_text", "") or ""
                ).strip()
                if raw_context_text:
                    context_text.append(raw_context_text)
                raw_instruction_skills = context.pop(
                    "instruction_skills",
                    (),
                )
                if isinstance(raw_instruction_skills, (list, tuple)):
                    for skill in raw_instruction_skills:
                        skill_id = str(skill or "").strip()
                        if skill_id and skill_id not in instruction_skills:
                            instruction_skills.append(skill_id)
                supplied_working_directory = str(
                    context.pop("working_directory", "") or ""
                ).strip()
                if supplied_working_directory:
                    if (
                        working_directory
                        and supplied_working_directory != working_directory
                    ):
                        raise ValueError(
                            "Resident input contains conflicting working directories"
                        )
                    working_directory = supplied_working_directory
                context_refs.update(context)
            else:
                context_refs.setdefault("resources", []).append(part)
        else:
            context_refs.setdefault("content_parts", []).append(part)
    # The stored part lengths locate inline attachments in the captured input.
    # Validation must not change the body that those lengths describe.
    content = "\n".join(text_parts)
    if not content.strip():
        raise ValueError("Resident input requires a text content part")
    return (
        content,
        media,
        context_refs,
        "\n\n".join(context_text),
        instruction_skills,
        working_directory,
    )
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


def message_identity(value: object = "") -> str:
    identity = stripped_text(value)
    if identity and not MESSAGE_IDENTITY_PATTERN.fullmatch(identity):
        raise ValueError("invalid MessageIdentity")
    return identity or f"msg_{secrets.token_hex(16)}"


def delivered_message_id(response: object) -> str:
    """Read only the identity acknowledged for this delivery, never a projection tail."""
    if not isinstance(response, Mapping):
        return ""
    direct = stripped_text(response.get("message_id"))
    message = response.get("message")
    nested = stripped_text(message.get("message_id")) if isinstance(message, Mapping) else ""
    if direct and nested and direct != nested:
        raise ValueError("Resident delivery acknowledgement has conflicting message IDs")
    return direct or nested

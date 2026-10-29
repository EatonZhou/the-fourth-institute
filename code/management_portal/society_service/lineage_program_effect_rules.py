"""How one Effect activation feeds its script (decision_4adcc1ff2f2c).

An Effect holds a script and named endpoints.  At activation the runtime
gives the script its inputs (the Route's arguments, Packet, triggering Event
and endpoint targets), derives one idempotency key per
call from the activation identity and the call's position in the script, and
composes ``DeliverMessage`` messages from the fixed prompt and this
activation's input.
"""
from __future__ import annotations

import hashlib
from typing import Any

from . import lineage_primitives, resident_message_contract
from .lineage_values import _text

DELIVER_MESSAGE = "DeliverMessage"
DEFAULT_CONTINUE_PROMPT = "请继续推进当前职责，并给出可复核结果。"
MAX_ROUTE_DEPTH = 24


def effect_definition(plan: dict[str, Any], effect_id: str) -> dict[str, Any]:
    definition = (plan.get("effects") or {}).get(effect_id) if isinstance(plan.get("effects"), dict) else None
    if not isinstance(definition, dict):
        raise ValueError("Effect component 已不存在")
    if not str(definition.get("script") or "").strip():
        raise ValueError("Effect 尚未编写脚本")
    return definition


def effect_script_inputs(definition: dict[str, Any], invocation: lineage_primitives.EffectInvocation) -> dict[str, Any]:
    """The four read-only script variables: arguments, event, packet, endpoint."""

    activation = invocation.activation
    payload = activation.payload if isinstance(activation.payload, dict) else {}
    event = {
        "id": activation.id,
        "type": activation.type,
        "subject_resident_id": activation.subject_resident_id,
        "occurred_at": activation.occurred_at,
        "payload": payload,
        "outputs": payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {},
    }
    return {
        "arguments": dict(invocation.arguments),
        "event": event if invocation.include_envelope else {},
        "packet": dict(invocation.packet or {}) if invocation.include_envelope else {},
        "endpoint": {
            str(name): str(target)
            for name, target in (definition.get("endpoints") or {}).items()
            if str(target)
        },
    }


def validate_effect_message(value: object) -> None:
    """Only Effect's string shorthand/omission extends the formal Message."""
    if value is None or isinstance(value, str):
        return
    resident_message_contract.validate_delivery_message(value)


def validate_effect_argument(instruction: str, name: str, value: object) -> None:
    if instruction == DELIVER_MESSAGE and name == "message":
        validate_effect_message(value)
        return
    from management_portal.hatg.catalog import instruction_catalog

    declaration = instruction_catalog().get(instruction)
    parameter = declaration.parameter_map.get(name) if declaration else None
    if parameter is not None:
        error = parameter.check(value)
        if error:
            raise ValueError(f"{instruction} 参数 {name} {error}")


def _message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n\n".join(
            str(part.get("text") or "")
            for part in value.get("content_parts") or []
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _delivery_message(
    fixed: object,
    invocation: lineage_primitives.EffectInvocation,
    *,
    include_input: bool = True,
) -> dict[str, Any]:
    """Fixed prompt, a blank line, then this activation's input.

    ``include_input=False`` leaves the input out when the fixed text
    already contains it.
    """

    validate_effect_message(fixed)
    fixed_text = lineage_primitives.normalize_invocation_prompt(_message_text(fixed).strip())
    input_text = lineage_primitives.normalize_invocation_prompt(
        str(invocation.arguments.get("prompt") or "").strip()
    ) if include_input else ""
    combined = "\n\n".join(part for part in (fixed_text, input_text) if part) or DEFAULT_CONTINUE_PROMPT
    # The authored text is bounded when saved; with the runtime input the
    # message shares the invocation budget and is never truncated.
    if len(combined) > lineage_primitives.MAX_INVOCATION_PROMPT_CHARS:
        raise ValueError(
            f"组合后的 prompt 超过 {lineage_primitives.MAX_INVOCATION_PROMPT_CHARS} 字符，不投递截断的消息"
        )
    parts: list[dict[str, Any]] = [{"type": "text", "text": combined}]
    if isinstance(fixed, dict):
        parts.extend(
            part for part in fixed.get("content_parts") or []
            if isinstance(part, dict) and part.get("type") != "text"
        )
    if invocation.arguments.get("content_parts"):
        parts.extend(lineage_primitives.normalize_invocation_content_parts(invocation.arguments["content_parts"]))
    return {**(dict(fixed) if isinstance(fixed, dict) else {}), "role": "user", "content_parts": parts}


def effect_idempotency_key(lineage_id: str, effect_id: str, invocation: lineage_primitives.EffectInvocation) -> str:
    """Stable per activation: the same Event reaching the same Effect is one activation."""

    material = "\0".join((lineage_id, effect_id, invocation.activation.id, invocation.id))
    return "effect:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def effect_call_key(activation_key: str, position: str) -> str:
    """One idempotency key per call: activation plus the call's position (statement and loop indices).

    The first call of a script outside any loop keeps the activation key
    itself, so a one-line script uses the same key a single-instruction
    Effect used and an activation replayed across the migration is not
    executed twice.
    """

    if position == "c1":
        return activation_key
    material = "\0".join((activation_key, position))
    return "effect:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


def prepare_call_arguments(
    instruction: str,
    arguments: dict[str, Any],
    invocation: lineage_primitives.EffectInvocation,
) -> dict[str, Any]:
    """Arguments handed to the instruction; ``DeliverMessage`` gets the composed message."""

    prepared = dict(arguments)
    if instruction == DELIVER_MESSAGE:
        message = prepared.get("message")
        validate_effect_message(message)
        input_text = str(invocation.arguments.get("prompt") or "").strip()
        # A script whose message already expands this activation's input (for
        # example "固定任务：${event.payload.prompt}") gets no second copy of it.
        repeated = bool(input_text) and input_text in _message_text(message)
        prepared["message"] = _delivery_message(message, invocation, include_input=not repeated)
    return prepared


def effect_failure_outputs(error: dict[str, Any]) -> dict[str, Any]:
    return {
        "error_code": _text(error.get("code") or "instruction.failed", 160),
        "diagnostic": _text(error.get("message") or "", 1000),
        "retryable": bool(error.get("retryable")),
        "failed_line": int(error.get("line") or 0),
        "completed_calls": int(error.get("completed_calls") or 0),
    }


def validate_effect_route(effect_id: str, route_path_value: object) -> None:
    route_path = [item for item in str(route_path_value or "").split(",") if item]
    if effect_id in route_path or len(route_path) >= MAX_ROUTE_DEPTH:
        raise ValueError(f"Effect 路由形成同步循环或超过 {MAX_ROUTE_DEPTH} 层")

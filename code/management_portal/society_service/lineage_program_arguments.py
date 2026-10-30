"""Invocation Argument Mappings: the route-owned mapping and its invoke profiles, the
profile a target uses, and resolving a mapping against one activation.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from . import lineage_primitives
from .lineage_program_vocabulary import PACKET_OPERATOR_KINDS
from .lineage_values import _text


_INVOKE_PROFILE_KEY_RE = re.compile(
    r"^(?:agent|filter|selector|reducer|loop|effect)$"
)
_EVENT_OUTPUT_REF_RE = re.compile(
    r"^\$event\.outputs\.([a-z][a-z0-9_]{0,79})$"
)
_INVOCATION_ARGUMENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_INVOCATION_ARGUMENT_SOURCE_RE = re.compile(
    r"^(?:"
    r"event\.(?:id|type|subject_resident_id|occurred_at|payload|outputs)"
    r"(?:\.[a-z][a-z0-9_]{0,79})*"
    r"|packet\.(?:id|key|priority|from_node_id|from_resident_id|source_wire_id|event_id|message|events|parent_packet_ids)"
    r"(?:\.(?:[a-z][a-z0-9_]{0,79}|[0-9]+))*"
    r"|invocation\.[a-z][a-z0-9_]{0,79}"
    r"|endpoint\.[a-z][a-z0-9_]{0,39}"
    r")$"
)


def event_output_reference_names(value: object) -> set[str]:
    """Collect exact typed Event output references from a JSON-like value."""

    result: set[str] = set()
    if isinstance(value, str):
        match = _EVENT_OUTPUT_REF_RE.fullmatch(value.strip())
        if match:
            result.add(match.group(1))
        elif value.strip().startswith("$event."):
            raise ValueError(
                "Event 输出引用必须使用 $event.outputs.<field>"
            )
        return result
    if isinstance(value, list):
        for item in value:
            result.update(event_output_reference_names(item))
        return result
    if isinstance(value, dict):
        for item in value.values():
            result.update(event_output_reference_names(item))
    return result


def _normalize_invocation_binding(
    raw: object,
    *,
    field: str,
    depth: int = 0,
) -> dict[str, Any]:
    if depth > 6:
        raise ValueError(f"Argument Mapping {field} 的 concat 嵌套过深")
    if not isinstance(raw, dict):
        raise ValueError(
            f"Argument Mapping {field} 必须声明 value、from 或 concat"
        )
    variants = [
        "value" in raw,
        bool(_text(raw.get("from"), 200)),
        "concat" in raw,
    ]
    if sum(1 for value in variants if value) != 1:
        raise ValueError(
            f"Argument Mapping {field} 必须且只能声明 value、from 或 concat"
        )
    if "value" in raw:
        unknown = sorted(set(raw) - {"value"})
        if unknown:
            raise ValueError(
                f"Argument Mapping {field} 的 value 不接受字段："
                + "、".join(unknown)
            )
        return {"value": deepcopy(raw["value"])}
    if variants[1]:
        unknown = sorted(set(raw) - {"from", "default"})
        if unknown:
            raise ValueError(
                f"Argument Mapping {field} 的 from 不接受字段："
                + "、".join(unknown)
            )
        source = _text(raw.get("from"), 200).lstrip("$")
        if not _INVOCATION_ARGUMENT_SOURCE_RE.fullmatch(source):
            raise ValueError(
                f"Argument Mapping {field} 的 from 不是受支持的 Invocation 来源"
            )
        binding: dict[str, Any] = {"from": source}
        if "default" in raw:
            binding["default"] = deepcopy(raw["default"])
        return binding
    unknown = sorted(set(raw) - {"concat", "separator"})
    if unknown:
        raise ValueError(
            f"Argument Mapping {field} 的 concat 不接受字段："
            + "、".join(unknown)
        )
    parts = raw.get("concat")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 24:
        raise ValueError(
            f"Argument Mapping {field} 的 concat 必须包含 1 到 24 个片段"
        )
    return {
        "concat": [
            _normalize_invocation_binding(
                part,
                field=f"{field}.concat[{index}]",
                depth=depth + 1,
            )
            for index, part in enumerate(parts)
        ],
        "separator": str(raw.get("separator") or "")[:200],
    }


def normalize_invocation_argument_mapping(raw: object) -> dict[str, dict[str, Any]]:
    """Normalize the one route-owned mapping into Invocation arguments.

    Each named argument is a JSON constant, a reference to the
    activation/packet envelope, or an explicit recursive ``concat``. This
    structured spelling keeps ordinary strings literal and makes branch
    contracts visible without introducing an implicit template language.
    """

    if raw is None:
        return {}
    bounded = lineage_primitives.normalize_argument_object(raw)
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_binding in bounded.items():
        name = _text(raw_name, 80)
        if not _INVOCATION_ARGUMENT_NAME_RE.fullmatch(name):
            raise ValueError(
                "Invocation Argument Mapping 的参数名必须是规范标识符"
            )
        normalized[name] = _normalize_invocation_binding(
            raw_binding,
            field=name,
        )
    return lineage_primitives.normalize_argument_object(normalized)


def normalize_invoke_profiles(raw: object) -> dict[str, dict[str, Any]]:
    """Normalize every target-contract profile owned by one Invoke wire.

    A wire keeps inactive profiles so changing the target Resident kind or an
    Effect's instruction type never destroys previously authored arguments.
    The compiler selects exactly one profile for each concrete target.
    """

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Invoke profiles 必须是 JSON object")
    profiles: dict[str, dict[str, Any]] = {}
    for raw_key, raw_profile in raw.items():
        key = _text(raw_key, 96).lower()
        if not _INVOKE_PROFILE_KEY_RE.fullmatch(key):
            raise ValueError(f"Invoke profile key 无效：{key or '（空）'}")
        if not isinstance(raw_profile, dict):
            raise ValueError(f"Invoke profile {key} 必须是 JSON object")
        unknown = sorted(set(raw_profile) - {"arguments"})
        if unknown:
            raise ValueError(
                f"Invoke profile {key} 不接受字段：" + "、".join(unknown)
            )
        arguments = normalize_invocation_argument_mapping(
            raw_profile.get("arguments")
        )
        profiles[key] = {"arguments": arguments}
    return profiles


def invoke_profile_key(
    target_id: object,
    *,
    residents: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> str:
    """Return the stable argument-contract key for one Invoke target."""

    normalized_id = _text(target_id, 110).lower()
    component = components.get(normalized_id)
    if component:
        component_kind = _text(component.get("kind"), 40).lower()
        if component_kind in {"effect", "loop", *PACKET_OPERATOR_KINDS}:
            return component_kind
    if normalized_id not in residents:
        raise ValueError("Invoke target 不在当前 HATG")
    return "agent"


def compiled_invocation(
    wire: dict[str, Any],
    *,
    residents: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Select the active Invocation arguments without discarding siblings."""

    profile_key = invoke_profile_key(
        wire.get("target"),
        residents=residents,
        components=components,
    )
    config = wire.get("config") if isinstance(wire.get("config"), dict) else {}
    profiles = normalize_invoke_profiles(config.get("invoke_profiles"))
    profile = profiles.get(profile_key) or {"arguments": {}}
    return {
        "profile_key": profile_key,
        "arguments": deepcopy(profile.get("arguments") or {}),
    }


def invocation_argument_output_reference_names(raw: object) -> set[str]:
    """Return typed Effect output names required by one route mapping."""

    mapping = normalize_invocation_argument_mapping(raw)
    prefix = "event.outputs."
    result: set[str] = set()

    def visit(binding: dict[str, Any]) -> None:
        source = str(binding.get("from") or "")
        if source.startswith(prefix):
            result.add(source[len(prefix):].split(".", 1)[0])
        for part in binding.get("concat") or []:
            if isinstance(part, dict):
                visit(part)

    for binding in mapping.values():
        visit(binding)
    return result


def resolve_invocation_argument_mapping(
    raw: object,
    event: lineage_primitives.LineageEvent | dict[str, Any],
    *,
    packet: dict[str, Any] | None = None,
    invocation_arguments: dict[str, Any] | None = None,
    endpoints: dict[str, str] | None = None,
    normalize_result: bool = True,
    include_envelope: bool = True,
) -> dict[str, Any]:
    """Resolve an argument mapping against its activation.

    Sources are constants, the Event or Packet envelope, the invocation
    arguments an Invoke wire supplied, named Effect endpoints and ``concat``.
    """

    mapping = normalize_invocation_argument_mapping(raw)
    activation = lineage_primitives.normalize_event(event)
    missing = object()
    packet_envelope = deepcopy(packet) if isinstance(packet, dict) else {}
    invocation_values = deepcopy(invocation_arguments) if isinstance(invocation_arguments, dict) else {}
    endpoint_values = dict(endpoints or {})

    def source_value(source: str) -> object:
        if source.startswith("invocation."):
            name = source.split(".", 1)[1]
            return deepcopy(invocation_values[name]) if name in invocation_values else missing
        if source.startswith("endpoint."):
            name = source.split(".", 1)[1]
            return endpoint_values[name] if endpoint_values.get(name) else missing
        if not include_envelope:
            return missing
        if source.startswith("packet."):
            root: object = packet_envelope
            for part in source.split(".")[1:]:
                if isinstance(root, list) and part.isdigit():
                    if int(part) >= len(root):
                        return missing
                    root = root[int(part)]
                    continue
                if not isinstance(root, dict) or part not in root:
                    return missing
                root = root[part]
            return root
        root: object = {
            "id": activation.id,
            "type": activation.type,
            "subject_resident_id": activation.subject_resident_id,
            "occurred_at": activation.occurred_at,
            "payload": activation.payload,
            "outputs": (
                activation.payload.get("outputs")
                if isinstance(activation.payload.get("outputs"), dict)
                else {}
            ),
        }
        for part in source.split(".")[1:]:
            if not isinstance(root, dict) or part not in root:
                return missing
            root = root[part]
        return root

    def resolve_binding(binding: dict[str, Any]) -> object:
        if "value" in binding:
            return deepcopy(binding["value"])
        if "from" in binding:
            value = source_value(str(binding["from"]))
            if value is missing:
                if "default" in binding:
                    value = binding["default"]
                else:
                    raise ValueError(
                        f"Invocation 来源 {binding['from']} 在当前触发中不存在"
                    )
            return deepcopy(value)
        values = [resolve_binding(part) for part in binding["concat"]]

        def concat_text(value: object) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            if isinstance(value, (dict, list)):
                return json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)

        return str(binding.get("separator") or "").join(
            concat_text(value) for value in values
        )

    resolved: dict[str, Any] = {}
    for name, binding in mapping.items():
        resolved[name] = resolve_binding(binding)
    if not normalize_result:
        return resolved
    return lineage_primitives.normalize_invocation_arguments(resolved)

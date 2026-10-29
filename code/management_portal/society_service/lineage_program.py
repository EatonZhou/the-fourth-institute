from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Iterable, NamedTuple

from management_common.limit_contract import normalize_optional_limit
from . import (
    lineage_graph_state,
    lineage_primitive_definitions,
    lineage_primitives,
    lineage_recurring_schedule,
)
from .lineage_values import _integer, _text, _item_id


PROGRAM_SCHEMA = "tfi.hatg-program.v2"
PACKET_OPERATOR_KINDS = {"filter", "selector", "reducer"}
PROGRAM_COMPONENT_KINDS = {
    "event_source",
    "effect",
    "seed",
    "loop",
    *PACKET_OPERATOR_KINDS,
}
PROGRAM_WIRE_KINDS = {"route", "target", "watch"}
EVENT_SOURCE_TYPES = set(
    lineage_primitive_definitions.type_definitions("event")
)
DEFAULT_EVENT_SOURCE_TYPE = lineage_primitive_definitions.default_type(
    "event"
)
EVENT_SOURCE_AUTHORING_CONFIG_FIELDS = (
    lineage_primitive_definitions.config_field_names("event")
)
# An Effect runs a script of instruction calls (decision_4adcc1ff2f2c "Effect：用脚本组合指令").
EFFECT_CONFIG_FIELDS = {"script", "endpoints", "failure_recipients"}
EFFECT_ENDPOINT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_EFFECT_ENDPOINTS = 8
LOOP_CONFIG_FIELDS = {"max_rounds", "completion"}
LOOP_COMPLETIONS = {"complete", "rearm"}
MAX_LOOP_ROUNDS = 1000
LOOP_INPUT_PORTS = {"invoke", "done"}
LOOP_OUTPUT_PORTS = {"continue", "stop"}
PACKET_OPERATOR_COMMON_CONFIG_FIELDS = {
    "operator_implementation",
    "resident_executor_id",
    "prompt",
}
PACKET_OPERATOR_CONFIG_FIELDS = {
    "filter": {
        *PACKET_OPERATOR_COMMON_CONFIG_FIELDS,
        "selection",
    },
    "selector": {
        *PACKET_OPERATOR_COMMON_CONFIG_FIELDS,
        "branches",
    },
    "reducer": {
        *PACKET_OPERATOR_COMMON_CONFIG_FIELDS,
        "reduce_strategy",
        "reduce_separator",
        "output_key",
    },
}
SEED_CONFIG_FIELDS = {
    "seed_type", "summary", "artifact_path", "source_resident_id", "status",
    "promoted_resident_id", "promotion_purpose", "created_by", "created_at", "promoted_at",
    "dismissed_at", "dismissed_by", "dismissal_reason",
    "dismissal_source", "updated_at",
}
# What happens to a Seed after it is created: adopted, dismissed.  A Function
# Block definition does not set these; re-expanding its instance keeps them.
SEED_LIFECYCLE_FIELDS = frozenset({
    "status", "promoted_resident_id", "promotion_purpose", "promoted_at",
    "dismissed_at", "dismissed_by", "dismissal_reason", "dismissal_source",
    "updated_at",
})
PACKET_OPERATOR_IMPLEMENTATIONS = {"rules", "resident"}
RULE_REDUCE_STRATEGIES = {"join", "first", "highest_priority"}
EFFECT_OUTCOME_EVENT_TYPE = "effect.succeeded"
EFFECT_FAILURE_EVENT_TYPE = "effect.failed"
EVENT_SOURCE_OBSERVER_TYPES = set(
    lineage_primitive_definitions.event_types_watching("event_source")
    | lineage_primitive_definitions.event_types_watching("wire")
)
EVENT_SOURCE_DELIVERY_OBSERVER_TYPES = set(
    lineage_primitive_definitions.event_types_watching("wire")
)
EVENT_SOURCE_EFFECT_OBSERVER_TYPES = set(
    lineage_primitive_definitions.event_types_watching("effect")
)
EVENT_SOURCE_HANDOFF_OBSERVER_TYPES = set(
    lineage_primitive_definitions.event_types_watching("handoff")
)
# An Event Source of these types may watch several Residents; any one of
# them fires it, and each Event names the Resident it is about.
EVENT_SOURCE_MULTI_RESIDENT_TYPES = set(
    lineage_primitive_definitions.event_types_watching_many()
)


def event_source_subject_ids(source: object) -> tuple[str, ...]:
    """The Residents a compiled Event Source watches: none, one or several."""

    if not isinstance(source, dict):
        return ()
    many = source.get("subject_resident_ids")
    if isinstance(many, (list, tuple)) and many:
        return tuple(dict.fromkeys(str(item) for item in many if str(item)))
    single = str(source.get("subject_resident_id") or "")
    return (single,) if single else ()


def watches_many_residents(source: object) -> bool:
    """Whether a compiled Event Source watches more than one Resident."""

    return len(event_source_subject_ids(source)) > 1


# A Resident finishing, failing or cancelling work may be observed for the
# Residents its Watch wires name ("watched"), every Resident of the Lineage
# ("lineage") or every Resident of Portal ("system").  A scoped source needs
# no Watch wire and covers Residents added later; it never fires for work it
# delivered itself, directly or through an Effect.
SCOPED_RESIDENT_EVENT_TYPES = frozenset({
    "resident.work_finished",
    "resident.work_failed",
    "resident.work_cancelled",
})
RESIDENT_SCOPES = ("watched", "lineage", "system")
# Event types whose Watch wire may name a Resident.
RESIDENT_SUBJECT_EVENT_TYPES = frozenset({
    "resident.work_finished",
    "resident.work_started",
    "resident.work_failed",
    "resident.work_cancelled",
    "resident.state_changed",
    "resident.context_pressure_exceeded",
    "resident.work_duration_exceeded",
    "resident.queue_pressure_exceeded",
    "resident.message_received",
    "file.changed",
})


def event_source_resident_scope(source: object) -> str:
    """``watched``, ``lineage`` or ``system`` for a compiled Event Source."""

    if not isinstance(source, dict):
        return "watched"
    if str(source.get("event_type") or "") not in SCOPED_RESIDENT_EVENT_TYPES:
        return "watched"
    scope = str(source.get("resident_scope") or "").strip().lower()
    return scope if scope in {"lineage", "system"} else "watched"


def _component_resident_scope(component: object) -> str:
    """The Resident scope an authored Event Source component declares."""

    if not isinstance(component, dict) or component.get("kind") != "event_source":
        return "watched"
    config = authoring_component_config(component)
    return event_source_resident_scope({
        "event_type": str(config.get("event_type") or "").strip().lower(),
        "resident_scope": config.get("resident_scope"),
    })


def event_source_covers(source: object, resident_id: str, member_ids: Iterable[str] = ()) -> bool:
    """Whether a compiled Event Source observes ``resident_id``.

    ``member_ids`` are the Residents of the source's own Lineage.
    """

    scope = event_source_resident_scope(source)
    if scope == "system":
        return bool(resident_id)
    if scope == "lineage":
        return bool(resident_id) and resident_id in set(member_ids)
    return resident_id in event_source_subject_ids(source)


def event_source_residents(source: object, member_ids: Iterable[str]) -> tuple[str, ...]:
    """The Residents of the source's Lineage it observes, or its watched Residents.

    A scoped source observes every member; Residents outside the Lineage
    reach a system-scope source only as their terminal facts arrive.
    """

    if event_source_resident_scope(source) == "watched":
        return event_source_subject_ids(source)
    return tuple(sorted({str(item) for item in member_ids if str(item)}))


def resident_completion_subscriptions(
    plan: dict[str, Any], member_ids: Iterable[str],
) -> list[tuple[str, tuple[str, ...], bool, list[str]]]:
    """Each ``resident.work_finished`` source as (id, observed Residents, per-Resident cursor, subscriber wire IDs).

    A source watching several Residents, or a scoped one, keeps one cursor
    per Resident.
    """

    members = tuple(member_ids)
    subscriptions = []
    for event_source_id, source in sorted((plan.get("event_sources") or {}).items()):
        if not isinstance(source, dict) or str(source.get("event_type") or "") != "resident.work_finished":
            continue
        watched = event_source_residents(source, members)
        per_resident = len(watched) > 1 or event_source_resident_scope(source) != "watched"
        wire_ids = [
            str(route.get("wire_id") or "")
            for route in source.get("subscriber_routes") or []
            if isinstance(route, dict) and str(route.get("wire_id") or "")
        ]
        subscriptions.append((str(event_source_id), watched, per_resident, wire_ids))
    return subscriptions
GRAPH_MUTATION_OPERATIONS = {"set", "add", "clone", "remove"}
GRAPH_MUTATION_KINDS = {"component", "wire"}
GRAPH_MUTATION_COMPONENT_FIELDS = {
    "client_key",
    "title",
    "x",
    "y",
    "config",
    *(
        f"config.{field_name}"
        for field_name in (
            EVENT_SOURCE_AUTHORING_CONFIG_FIELDS
            | EFFECT_CONFIG_FIELDS
            | LOOP_CONFIG_FIELDS
            | SEED_CONFIG_FIELDS
            | set().union(*PACKET_OPERATOR_CONFIG_FIELDS.values())
        )
    ),
}
GRAPH_MUTATION_WIRE_FIELDS = {
    "client_key",
    "source",
    "source_port",
    "target",
    "target_port",
    "label",
    "config",
    "config.invoke_profiles",
    "config.include_envelope",
    "config.parallel",
    "config.unavailable_policy",
    "config.queue_limit",
    "config.max_messages",
    "config.expires_after_seconds",
}
MAX_TITLE_CHARS = 120
MAX_PATH_CHARS = 800
EVENT_UNAVAILABLE_POLICIES = {"drop", "latest", "queue"}
DEFAULT_EVENT_UNAVAILABLE_POLICY = "drop"
DEFAULT_EVENT_QUEUE_LIMIT = 10
MAX_EVENT_QUEUE_LIMIT = 100
MAX_EVENT_EXPIRES_AFTER_SECONDS = 7 * 24 * 60 * 60
MAX_EVENT_FIRES = 100_000
STATE_CONDITION_OPERATORS = {
    "exists",
    "missing",
    "equals",
    "not_equals",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
}

_COMPONENT_ID_RE = re.compile(
    r"^(?:event_source|effect|seed|loop|filter|selector|reducer)_"
    r"[a-z0-9][a-z0-9_-]{2,95}$"
)
_WIRE_ID_RE = re.compile(r"^wire_[a-z0-9][a-z0-9_-]{2,95}$")
_CLIENT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_INVOKE_PROFILE_KEY_RE = re.compile(
    r"^(?:agent|filter|selector|reducer|loop|effect)$"
)
_FUNCTION_BLOCK_ID_RE = re.compile(r"^fbi_[0-9a-f]{12}$")
_LINEAGE_ID_RE = re.compile(r"^lineage_[0-9a-f]{12}$")
_RESIDENT_ID_RE = lineage_primitives.RESIDENT_ID_RE
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
_GRAPH_MUTATION_FIELD_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?$"
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


def authoring_component_config(
    component: dict[str, Any],
) -> dict[str, Any]:
    """Return the only persisted/API configuration boundary for a component."""

    config = component.get("config")
    if not isinstance(config, dict):
        component_id = str(component.get("id") or "（未知组件）")
        raise ValueError(f"Program component {component_id} 缺少 config")
    return config


def authoring_event_type(component: dict[str, Any]) -> str:
    """Read an Event type from the canonical authoring configuration."""

    event_type = _text(
        authoring_component_config(component).get("event_type"),
        80,
    ).lower()
    if event_type not in EVENT_SOURCE_TYPES:
        raise ValueError(f"未知的 Event Source 类型：{event_type or '（空）'}")
    return event_type


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


def normalize_graph_mutations(raw: object) -> list[dict[str, Any]]:
    """Normalize the bounded graph-mutation language stored by one Effect."""

    values = raw if isinstance(raw, list) else []
    if len(values) > 64:
        raise ValueError("Graph Mutation 每次最多包含 64 个操作")
    normalized: list[dict[str, Any]] = []
    for index, raw_value in enumerate(values):
        item = raw_value if isinstance(raw_value, dict) else {}
        operation = _text(item.get("op"), 20).lower()
        kind = _text(item.get("kind"), 20).lower()
        if operation not in GRAPH_MUTATION_OPERATIONS:
            raise ValueError(
                f"Graph Mutation 第 {index + 1} 项 op 无效"
            )
        if kind not in GRAPH_MUTATION_KINDS:
            raise ValueError(
                f"Graph Mutation 第 {index + 1} 项 kind 无效"
            )
        result: dict[str, Any] = {
            "op": operation,
            "kind": kind,
        }
        identifier_pattern = (
            _COMPONENT_ID_RE if kind == "component" else _WIRE_ID_RE
        )
        if operation in {"set", "clone", "remove"}:
            item_id = _text(item.get("id"), 110).lower()
            if not identifier_pattern.fullmatch(item_id):
                raise ValueError(
                    f"Graph Mutation 第 {index + 1} 项 id 无效"
                )
            result["id"] = item_id
        if operation == "set":
            field_name = _text(item.get("field"), 80)
            allowed_fields = (
                GRAPH_MUTATION_COMPONENT_FIELDS
                if kind == "component"
                else GRAPH_MUTATION_WIRE_FIELDS
            )
            if (
                not _GRAPH_MUTATION_FIELD_RE.fullmatch(field_name)
                or field_name not in allowed_fields
            ):
                raise ValueError(
                    f"Graph Mutation 不允许修改 {kind}.{field_name or '（空）'}"
                )
            if "value" not in item:
                raise ValueError("Graph Mutation set 操作缺少 value")
            event_output_reference_names(item.get("value"))
            result["field"] = field_name
            result["value"] = deepcopy(item.get("value"))
        elif operation == "add":
            value = item.get("value")
            if not isinstance(value, dict):
                raise ValueError("Graph Mutation add 操作需要对象 value")
            item_id = _text(value.get("id"), 110).lower()
            if not identifier_pattern.fullmatch(item_id):
                raise ValueError("Graph Mutation add value.id 无效")
            allowed_add_fields = (
                {
                    "id",
                    "kind",
                    "client_key",
                    "title",
                    "x",
                    "y",
                    "config",
                    *GRAPH_MUTATION_COMPONENT_FIELDS,
                }
                if kind == "component"
                else {
                    "id",
                    "kind",
                    "type",
                    "client_key",
                    "source",
                    "source_port",
                    "target",
                    "target_port",
                    "label",
                    "config",
                }
            )
            unknown_fields = sorted(
                set(value) - allowed_add_fields
            )
            if unknown_fields:
                raise ValueError(
                    "Graph Mutation add 包含不可写字段："
                    + "、".join(unknown_fields)
                )
            event_output_reference_names(value)
            result["value"] = deepcopy(value)
        elif operation == "clone":
            new_id = _text(item.get("new_id"), 110).lower()
            if not identifier_pattern.fullmatch(new_id):
                raise ValueError("Graph Mutation clone new_id 无效")
            overrides = item.get("overrides")
            if overrides is not None and not isinstance(overrides, dict):
                raise ValueError(
                    "Graph Mutation clone overrides 必须是对象"
                )
            allowed_override_fields = (
                GRAPH_MUTATION_COMPONENT_FIELDS
                if kind == "component"
                else GRAPH_MUTATION_WIRE_FIELDS
            )
            unknown_fields = sorted(
                set(overrides or {}) - allowed_override_fields
            )
            if unknown_fields:
                raise ValueError(
                    "Graph Mutation clone overrides 包含不可写字段："
                    + "、".join(unknown_fields)
                )
            event_output_reference_names(overrides or {})
            result["new_id"] = new_id
            if overrides:
                result["overrides"] = deepcopy(overrides)
        normalized.append(result)
    return normalized


def parse_program_resource_suffix(suffix: object) -> tuple[str, str]:
    """Parse the one canonical nested address contract for HATG program items.

    Event Sources, Effects, typed wires and persistent Function Block
    boundaries are owned by a Lineage.  Their local ids are intentionally not
    registered as top-level TFI Portal resource ids; the parent Lineage remains
    the authority for identity, access and lifecycle.
    """

    clean = str(suffix or "").strip().strip("/")
    parts = clean.split("/") if clean else []
    if len(parts) != 3 or parts[0] != "program":
        raise ValueError(
            "Lineage program address must use "
            "program/component/<component_id>, program/wire/<wire_id>, "
            "or program/function-block/<instance_id>"
        )
    namespace, item_id = parts[1], parts[2]
    if namespace == "component":
        if not _COMPONENT_ID_RE.fullmatch(item_id):
            raise ValueError("Lineage program component address has an invalid id")
        return namespace, item_id
    if namespace == "wire":
        if not _WIRE_ID_RE.fullmatch(item_id):
            raise ValueError("Lineage program wire address has an invalid id")
        return namespace, item_id
    if namespace == "function-block":
        if not _FUNCTION_BLOCK_ID_RE.fullmatch(item_id):
            raise ValueError(
                "Lineage Function Block address has an invalid id"
            )
        return namespace, item_id
    raise ValueError(f"unsupported Lineage program address namespace: {namespace}")


def _program_resource_address(
    lineage_id: object,
    namespace: str,
    item_id: object,
) -> str:
    root_id = str(lineage_id or "").strip().lower()
    if not _LINEAGE_ID_RE.fullmatch(root_id):
        raise ValueError("Lineage program resource requires a canonical lineage_* id")
    parsed_namespace, parsed_item_id = parse_program_resource_suffix(
        f"program/{namespace}/{str(item_id or '').strip().lower()}"
    )
    return f"{root_id}/program/{parsed_namespace}/{parsed_item_id}"


def program_component_address(lineage_id: object, component_id: object) -> str:
    return _program_resource_address(lineage_id, "component", component_id)


def find_program_resource(
    definition: object,
    suffix: object,
) -> tuple[str, str, dict[str, Any] | None]:
    """Return ``(namespace, local_id, item)`` without inventing another store."""

    namespace, item_id = parse_program_resource_suffix(suffix)
    body = definition if isinstance(definition, dict) else {}
    payload = body if namespace == "function-block" else body.get("program", {})
    collection = {
        "component": "components",
        "wire": "wires",
        "function-block": "function_blocks",
    }[namespace]
    item = next(
        (
            value
            for value in payload.get(collection) or []
            if isinstance(value, dict)
            and str(value.get("id") or "").strip().lower() == item_id
        ),
        None,
    )
    return namespace, item_id, deepcopy(item) if item is not None else None


def _reject_unknown_fields(value: dict[str, Any], allowed: Iterable[str], subject: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{subject} 不接受字段：" + "、".join(unknown))


def _client_key(item: dict[str, Any]) -> dict[str, str]:
    client_key = _text(item.get("client_key"), 160)
    if not client_key:
        return {}
    if not _CLIENT_KEY_RE.fullmatch(client_key):
        raise ValueError(
            "Program client_key 必须以字母或数字开头，且只能包含"
            "字母、数字、点、下划线、冒号和连字符"
        )
    return {"client_key": client_key}


def _boolean(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value, 16).lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _position(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return round(number, 2)


def _percentage_threshold(
    value: object,
    *,
    default: float = 85.0,
) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        number = default
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "百分比阈值必须是 0 到 100 之间的数字"
            ) from exc
    if not math.isfinite(number) or number < 0 or number > 100:
        raise ValueError("百分比阈值必须在 0 到 100 之间")
    return round(number, 1)


def _bounded_integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    field: str,
) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        number = default
    else:
        if isinstance(value, bool):
            raise ValueError(f"{field} 必须是整数")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 必须是整数") from exc
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise ValueError(f"{field} 必须是整数")
        number = int(numeric)
    if number < minimum or number > maximum:
        raise ValueError(
            f"{field} 必须在 {minimum} 到 {maximum} 之间"
        )
    return number


def _relative_path(value: object, *, required: bool = True) -> str:
    return lineage_primitives.normalize_workspace_relative_path(
        value,
        required=required,
        max_chars=MAX_PATH_CHARS,
    )


def _iso_datetime(value: object) -> str:
    raw = _text(value, 80)
    if not raw:
        raise ValueError("time.reached Event Source 必须设置触发时间")
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Event Source 时间必须使用有效的 ISO 日期时间") from exc
    return raw


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\0".join(str(part or "") for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _resident_id(value: object, *, field: str) -> str:
    resident_id = _text(value, 80).lower()
    if not _RESIDENT_ID_RE.fullmatch(resident_id):
        raise ValueError(f"{field} 必须是当前 HATG 中规范的 resident_* ID")
    return resident_id


def default_packet_operator_config(kind: str) -> dict[str, Any]:
    """Return the authored defaults for one stable Packet Operator node."""

    clean_kind = _text(kind, 32).lower()
    if clean_kind == "filter":
        return {
            "operator_implementation": "rules",
            "prompt": (
                "比较同一 Event 下的候选 Packet，只保留符合当前下游契约"
                "的候选，不改写候选内容。"
            ),
            "selection": {
                "mode": "all",
                "conditions": [],
                "order_by": "priority_desc",
                "limit": 1,
            },
        }
    if clean_kind == "selector":
        return {
            "operator_implementation": "rules",
            "prompt": (
                "把候选 Packet 路由到合适的具名输出分支；不改写候选内容。"
            ),
            "branches": [
                {
                    "id": "branch_a",
                    "label": "分支 A",
                    "description": "接收路由标签为 branch_a 的 Packet。",
                    "match": {
                        "mode": "all",
                        "conditions": [
                            {
                                "from": "packet.key",
                                "operator": "equals",
                                "value": "branch_a",
                            }
                        ],
                        "order_by": "input",
                        "limit": None,
                    },
                },
                {
                    "id": "branch_b",
                    "label": "分支 B",
                    "description": "接收路由标签为 branch_b 的 Packet。",
                    "match": {
                        "mode": "all",
                        "conditions": [
                            {
                                "from": "packet.key",
                                "operator": "equals",
                                "value": "branch_b",
                            }
                        ],
                        "order_by": "input",
                        "limit": None,
                    },
                },
            ],
        }
    if clean_kind == "reducer":
        return {
            "operator_implementation": "resident",
            "prompt": (
                "保留每个上游的重要判断，消除重复与冲突，合并为一条清楚、"
                "可执行的 Packet。"
            ),
            "reduce_strategy": "join",
            "reduce_separator": "\n\n",
            "output_key": "reduced",
        }
    raise ValueError("Packet Operator kind 无效")


def normalize_packet_operator_config(
    kind: object,
    raw: object,
    *,
    residents: dict[str, dict[str, Any]] | None = None,
    component_id: str = "",
    require_executor: bool = True,
) -> dict[str, Any]:
    """Normalize authoring config without conflating node and executor IDs."""

    clean_kind = _text(kind, 32).lower()
    if clean_kind not in PACKET_OPERATOR_KINDS:
        raise ValueError("Packet Operator kind 无效")
    if not isinstance(raw, dict):
        raise ValueError(f"{clean_kind} component 必须把作者配置放在 config")
    _reject_unknown_fields(raw, PACKET_OPERATOR_CONFIG_FIELDS[clean_kind], f"{clean_kind} config")
    defaults = default_packet_operator_config(clean_kind)
    implementation = _text(
        raw.get("operator_implementation"),
        24,
    ).lower() or str(defaults["operator_implementation"])
    if implementation not in PACKET_OPERATOR_IMPLEMENTATIONS:
        raise ValueError("Packet Operator implementation 必须是 rules 或 resident")
    result: dict[str, Any] = {
        "operator_implementation": implementation,
        "prompt": _text(
            raw.get("prompt") if "prompt" in raw else defaults.get("prompt"),
            12000,
        ),
    }
    executor_id = _text(raw.get("resident_executor_id"), 80).lower()
    if executor_id:
        executor_id = _resident_id(
            executor_id,
            field="Packet Operator resident_executor_id",
        )
        if residents is not None:
            executor = residents.get(executor_id)
            if not executor:
                raise ValueError("Packet Operator 的 Resident 执行器不在当前 HATG")
            marker = (
                executor.get("operator_executor")
                if isinstance(executor.get("operator_executor"), dict)
                else {}
            )
            # Any member may execute an operator; a Resident created as the
            # dedicated executor of another operator stays with that one.
            if marker.get("component_id") and str(marker.get("component_id")) != component_id:
                raise ValueError("这个 Resident 是另一个 Packet Operator 的专用执行器")
        result["resident_executor_id"] = executor_id
    if implementation == "resident" and not executor_id and require_executor:
        raise ValueError("Resident 式 Packet Operator 必须绑定 Resident 执行器")

    if clean_kind == "filter":
        result["selection"] = lineage_primitives.normalize_packet_filter(
            raw.get("selection", defaults["selection"])
        )
    elif clean_kind == "selector":
        result["branches"] = lineage_primitives.normalize_selector_branches(
            raw.get("branches", defaults["branches"])
        )
    else:
        strategy = _text(
            raw.get("reduce_strategy", defaults["reduce_strategy"]),
            40,
        ).lower() or "join"
        if strategy not in RULE_REDUCE_STRATEGIES:
            raise ValueError("Reducer rule strategy 无效")
        output_key = _text(
            raw.get("output_key", defaults["output_key"]),
            lineage_primitives.MAX_PACKET_KEY_CHARS,
        ).lower() or "reduced"
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", output_key):
            raise ValueError("Reducer output_key 无效")
        result.update(
            {
                "reduce_strategy": strategy,
                "reduce_separator": str(
                    raw.get("reduce_separator", defaults["reduce_separator"])
                    or "\n\n"
                )[:200],
                "output_key": output_key,
            }
        )
    return result


def normalize_effect_config(config: dict[str, Any]) -> dict[str, Any]:
    """Shape of an Effect: its script and named endpoints.

    Syntax, instructions, arguments and references are reported by
    diagnostics, so a half-written script can be saved.
    """

    from .lineage_effect_script import MAX_SCRIPT_CHARS

    unknown = sorted(set(config) - EFFECT_CONFIG_FIELDS)
    if unknown:
        raise ValueError("Effect config 只包含 script、endpoints、failure_recipients；不接受：" + "、".join(unknown))
    raw_script = config.get("script")
    if raw_script is None:
        raw_script = ""
    if not isinstance(raw_script, str):
        raise ValueError("Effect 的 script 必须是文本")
    script = raw_script.replace("\r\n", "\n").replace("\r", "\n")
    if len(script) > MAX_SCRIPT_CHARS:
        raise ValueError(f"Effect 脚本最长 {MAX_SCRIPT_CHARS} 字符")
    raw_endpoints = config.get("endpoints") or []
    if not isinstance(raw_endpoints, list) or len(raw_endpoints) > MAX_EFFECT_ENDPOINTS:
        raise ValueError(f"Effect endpoints 必须是最多 {MAX_EFFECT_ENDPOINTS} 个名称的数组")
    endpoints: list[str] = []
    for raw in raw_endpoints:
        name = _text(raw, 40)
        if not EFFECT_ENDPOINT_RE.fullmatch(name) or name in endpoints:
            raise ValueError(f"Effect endpoint 名称无效或重复：{name or '（空）'}")
        endpoints.append(name)
    result = {"script": script, "endpoints": endpoints}
    if "failure_recipients" in config:
        from management_common.principal import actor_principal
        recipients = config["failure_recipients"]
        if not isinstance(recipients, list) or len(recipients) > 20:
            raise ValueError("Effect failure_recipients 必须是最多 20 个用户名的数组")
        users = []
        for username in recipients:
            if not isinstance(username, str) or not username.strip() or len(username) > 120:
                raise ValueError("Effect 失败通知收件人必须是用户名")
            username = username.strip()
            if actor_principal(username).kind != "user":
                raise ValueError("Effect 失败通知收件人只能是 User")
            if username not in users:
                users.append(username)
        result["failure_recipients"] = users
    return result


_COMPONENT_ENVELOPE_FIELDS = frozenset({
    "id", "kind", "client_key", "created_by_principal_id", "title", "x", "y", "config",
})


def _component_identity(item: dict[str, Any]) -> tuple[str, str]:
    component_id = _text(item.get("id"), 110).lower()
    if not _COMPONENT_ID_RE.fullmatch(component_id):
        raise ValueError("Lineage 原语 component id 无效")
    kind = _text(item.get("kind"), 32).lower()
    if kind not in PROGRAM_COMPONENT_KINDS:
        raise ValueError("Lineage 原语 component kind 无效")
    expected_prefix = f"{kind}_"
    if not component_id.startswith(expected_prefix):
        raise ValueError(f"{kind} component id 必须以 {expected_prefix} 开头")
    return component_id, kind


def _component_envelope(item: dict[str, Any]) -> dict[str, Any]:
    """Client key, creator, title and canvas position of one component."""

    result: dict[str, Any] = _client_key(item)
    created_by_principal_id = _text(item.get("created_by_principal_id"), 110).lower()
    if created_by_principal_id:
        if not _RESIDENT_ID_RE.fullmatch(created_by_principal_id):
            raise ValueError("Program component 创建者 Resident ID 无效")
        result["created_by_principal_id"] = created_by_principal_id
    title = _text(item.get("title"), MAX_TITLE_CHARS)
    if title:
        result["title"] = title
    for axis in ("x", "y"):
        if item.get(axis) is not None:
            result[axis] = _position(item[axis], 50)
    return result


def _authored_config(item: dict[str, Any], kind: str) -> dict[str, Any]:
    if not isinstance(item.get("config"), dict):
        raise ValueError(f"{kind} component 必须把作者配置放在 config")
    _reject_unknown_fields(item, _COMPONENT_ENVELOPE_FIELDS, f"{kind} component")
    return item["config"]


def _loop_config(config: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(config, LOOP_CONFIG_FIELDS, "loop config")
    completion = _text(config.get("completion"), 24).lower() or "complete"
    if completion not in LOOP_COMPLETIONS:
        raise ValueError("循环组件的 completion 只能是 complete 或 rearm")
    return {
        "max_rounds": _bounded_integer(
            config.get("max_rounds"), default=3, minimum=1, maximum=MAX_LOOP_ROUNDS,
            field="循环组件的最大轮数",
        ),
        "completion": completion,
    }


_SEED_TYPES = frozenset({"idea", "plan", "next_step", "other"})
_SEED_STATUSES = frozenset({"active", "promoted", "dismissed"})
# Seed text fields in their stored order, with their length limits.
_SEED_TEXT_LIMITS = {
    "summary": 800, "artifact_path": MAX_PATH_CHARS,
    "source_resident_id": 80, "promoted_resident_id": 80, "promotion_purpose": 4000,
    "created_by": 120, "created_at": 80, "promoted_at": 80,
    "dismissed_at": 80, "dismissed_by": 160,
    "dismissal_reason": 2000, "dismissal_source": 40,
    "updated_at": 80,
}
_SEED_RESIDENT_FIELDS = frozenset({"source_resident_id", "promoted_resident_id"})


def _seed_config(config: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(config, SEED_CONFIG_FIELDS, "seed config")
    seed_type = _text(config.get("seed_type"), 24).lower() or "other"
    if seed_type not in _SEED_TYPES:
        raise ValueError("Seed 类型无效")
    status = _text(config.get("status"), 24).lower() or "active"
    if status not in _SEED_STATUSES:
        raise ValueError("Seed 状态无效")
    result: dict[str, Any] = {"seed_type": seed_type, "status": status}
    for field_name, limit in _SEED_TEXT_LIMITS.items():
        value = _text(config.get(field_name), limit)
        if value and field_name in _SEED_RESIDENT_FIELDS:
            value = _resident_id(value, field=f"Seed {field_name}")
        if value:
            result[field_name] = value
    return result


def _script_finished_outcome(config: dict[str, Any]) -> dict[str, Any]:
    outcome = config.get("outcome") or "any"
    if outcome not in {"any", "succeeded", "failed"}:
        raise ValueError("script.finished outcome 无效")
    return {"outcome": outcome}


def _fire_budget(config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "max_fires": normalize_optional_limit(config.get("max_fires"), maximum=MAX_EVENT_FIRES),
    }
    auto_delete = config.get("auto_delete_on_exhaustion")
    if auto_delete is not None and not isinstance(auto_delete, bool):
        raise ValueError("auto_delete_on_exhaustion 必须是 boolean")
    if auto_delete:
        result["auto_delete_on_exhaustion"] = True
    return result


def _intake_state_domain(config: dict[str, Any]) -> dict[str, Any]:
    state_domain = _text(config.get("state_domain"), 40).lower() or "intake"
    if state_domain != "intake":
        raise ValueError("Resident 状态 Event 的状态域只能是 intake")
    return {"state_domain": state_domain}


def _changed_state_key(config: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"key": lineage_graph_state.normalize_state_key(config.get("key"))}
    except ValueError as exc:
        raise ValueError("state.changed Event 需要有效的 key") from exc


def _context_pressure_threshold(config: dict[str, Any]) -> dict[str, Any]:
    return {"threshold_percent": _percentage_threshold(config.get("threshold_percent"))}


def _work_duration_threshold(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_seconds": _bounded_integer(
            config.get("duration_seconds"), default=900, minimum=1,
            maximum=MAX_EVENT_EXPIRES_AFTER_SECONDS, field="Resident 单轮工作时长阈值",
        ),
    }


def _queue_pressure_threshold(config: dict[str, Any]) -> dict[str, Any]:
    pressure_metric = _text(config.get("pressure_metric"), 40).lower() or "queue_length"
    if pressure_metric not in {"queue_length", "oldest_wait_seconds"}:
        raise ValueError("Resident 队列压力指标无效")
    if pressure_metric == "queue_length":
        return {
            "pressure_metric": pressure_metric,
            "queue_length_threshold": _bounded_integer(
                config.get("queue_length_threshold"), default=3, minimum=1, maximum=100,
                field="Resident 队列长度阈值",
            ),
        }
    return {
        "pressure_metric": pressure_metric,
        "wait_seconds_threshold": _bounded_integer(
            config.get("wait_seconds_threshold"), default=300, minimum=1,
            maximum=MAX_EVENT_EXPIRES_AFTER_SECONDS, field="Resident 队列等待时长阈值",
        ),
    }


def _state_condition(config: dict[str, Any]) -> dict[str, Any]:
    try:
        state_key = lineage_graph_state.normalize_state_key(config.get("key"))
    except ValueError as exc:
        raise ValueError("State 条件 Event 需要有效的 key") from exc
    operator = _text(config.get("operator"), 40).lower() or "equals"
    if operator not in STATE_CONDITION_OPERATORS:
        raise ValueError("State 条件 Event 的 operator 无效")
    return {
        "key": state_key,
        "operator": operator,
        "expected_value": (
            None
            if operator in {"exists", "missing"}
            else lineage_graph_state.normalize_state_value(config.get("expected_value"))
        ),
        "fire_on_initial_match": _boolean(config.get("fire_on_initial_match"), False),
    }


def _all_residents_scope(_config: dict[str, Any]) -> dict[str, Any]:
    # This public Event has one stable meaning: every Resident known to
    # Portal has transitioned from active to idle.
    return {"resident_scope": "system"}


def _terminal_resident_scope(config: dict[str, Any]) -> dict[str, Any]:
    resident_scope = _text(config.get("resident_scope"), 24).lower() or "watched"
    if resident_scope not in RESIDENT_SCOPES:
        raise ValueError(
            "Resident 结束工作 Event 的监听范围只能是 "
            "watched、lineage 或 system"
        )
    return {} if resident_scope == "watched" else {"resident_scope": resident_scope}


def _finished_residents(config: dict[str, Any]) -> dict[str, Any]:
    raw_resident_ids = config.get("resident_ids")
    if not isinstance(raw_resident_ids, list):
        raise ValueError("residents.all_finished 的 resident_ids 必须是数组")
    resident_ids = list(dict.fromkeys(
        lineage_primitives.normalize_resident_id(
            value, field_name="residents.all_finished resident_id",
        )
        for value in raw_resident_ids
    ))
    if not resident_ids:
        raise ValueError("residents.all_finished 至少需要一个 Resident")
    trigger_path_id = _text(config.get("trigger_path_id"), 240) or "$trigger_path.id"
    if trigger_path_id != "$trigger_path.id" and not trigger_path_id.startswith("trigger_path_"):
        raise ValueError(
            "residents.all_finished trigger_path_id 必须是 $trigger_path.id 或 trigger_path_*"
        )
    return {"resident_ids": resident_ids, "trigger_path_id": trigger_path_id}


def _changed_file_path(config: dict[str, Any]) -> dict[str, Any]:
    path = _relative_path(config.get("path"), required=False)
    return {"path": path} if path else {}


def _release_misfire_policy(config: dict[str, Any]) -> dict[str, Any]:
    policy = _text(config.get("misfire_policy"), 24)
    if policy and policy not in {"all", "coalesce_latest"}:
        raise ValueError("发布 Event 的 misfire_policy 必须是 all 或 coalesce_latest")
    return {"misfire_policy": policy} if policy else {}


_RECURRING_SCHEDULE_FIELDS = (
    "recurrence_kind",
    "interval_seconds",
    "timezone",
    "calendar_frequency",
    "calendar_time",
    "calendar_weekdays",
    "calendar_day",
    "misfire_policy",
    "overlap_policy",
    "jitter_seconds",
)


def _time_schedule(config: dict[str, Any]) -> dict[str, Any]:
    schedule_mode = _text(config.get("schedule_mode"), 24).lower() or "at"
    if schedule_mode not in {"at", "countdown", "recurring"}:
        raise ValueError("时间 Event 的模式必须是 at、countdown 或 recurring")
    result: dict[str, Any] = {"schedule_mode": schedule_mode}
    if schedule_mode == "countdown":
        result["delay_seconds"] = max(
            1, min(MAX_EVENT_EXPIRES_AFTER_SECONDS, _integer(config.get("delay_seconds"), 60)),
        )
    elif schedule_mode == "at":
        scheduled_at = _text(config.get("scheduled_at"), 80)
        if scheduled_at:
            result["scheduled_at"] = _iso_datetime(scheduled_at)
    else:
        result.update(lineage_recurring_schedule.normalize_config(
            {key: config.get(key) for key in _RECURRING_SCHEDULE_FIELDS}
        ))
    return result


# The config fields of an Event Source, in the order they are checked: each
# step applies to the listed Event types and returns the fields it owns.
# The observed object is a graph relationship, not Event Source configuration;
# only an incoming Watch wire represents it.
_EVENT_SOURCE_CONFIG_STEPS: tuple[tuple[frozenset[str], Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    (frozenset({"script.finished"}), _script_finished_outcome),
    (frozenset(EVENT_SOURCE_TYPES), _fire_budget),
    (frozenset({"resident.state_changed"}), _intake_state_domain),
    (frozenset({"state.changed"}), _changed_state_key),
    (frozenset({"resident.context_pressure_exceeded"}), _context_pressure_threshold),
    (frozenset({"resident.work_duration_exceeded"}), _work_duration_threshold),
    (frozenset({"resident.queue_pressure_exceeded"}), _queue_pressure_threshold),
    (frozenset({"state.condition_met"}), _state_condition),
    (frozenset({"residents.all_idle"}), _all_residents_scope),
    (SCOPED_RESIDENT_EVENT_TYPES, _terminal_resident_scope),
    (frozenset({"residents.all_finished"}), _finished_residents),
    (frozenset({"file.changed"}), _changed_file_path),
    (frozenset({"portal.release_published"}), _release_misfire_policy),
    (frozenset({"time.reached"}), _time_schedule),
)


def _event_source_config(config: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_fields(config, EVENT_SOURCE_AUTHORING_CONFIG_FIELDS, "event_source config")
    event_type = _text(config.get("event_type"), 80).lower() or DEFAULT_EVENT_SOURCE_TYPE
    if event_type not in EVENT_SOURCE_TYPES:
        raise ValueError(f"未知的 Event Source 类型：{event_type}")
    _reject_unknown_fields(
        config,
        lineage_primitive_definitions.authoring_config_fields("event", event_type, config=config),
        f"Event Source {event_type} config",
    )
    fields: dict[str, Any] = {}
    for event_types, normalize in _EVENT_SOURCE_CONFIG_STEPS:
        if event_type in event_types:
            fields.update(normalize(config))
    return {"event_type": event_type, **{name: fields[name] for name in sorted(fields)}}


_COMPONENT_CONFIG_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "event_source": _event_source_config,
    "effect": normalize_effect_config,
    "seed": _seed_config,
    "loop": _loop_config,
}


def _normalize_component(
    raw: object,
    residents: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    component_id, kind = _component_identity(item)
    result: dict[str, Any] = {"id": component_id, "kind": kind, **_component_envelope(item)}
    config = _authored_config(item, kind)
    if kind in PACKET_OPERATOR_KINDS:
        result["config"] = normalize_packet_operator_config(
            kind,
            config,
            residents=residents,
            component_id=component_id,
            require_executor=False,
        )
    else:
        result["config"] = _COMPONENT_CONFIG_NORMALIZERS[kind](config)
    return result


_HANDOFF_ID_RE = re.compile(r"^handoff_[a-z0-9][a-z0-9_-]{2,95}$")
_INVOKE_TARGET_KINDS = {"effect", "seed", "loop", *PACKET_OPERATOR_KINDS}
_ROUTE_CONFIG_FIELDS = frozenset({
    "invoke_profiles",
    "include_envelope",
    "unavailable_policy",
    "queue_limit",
    "max_messages",
    "expires_after_seconds",
    "context_policy",
    "parallel",
})


class _WireEnds(NamedTuple):
    """What one wire connects: each end is a Resident, a component or neither."""

    source: str
    target: str
    source_resident: dict[str, Any] | None
    target_resident: dict[str, Any] | None
    source_component: dict[str, Any] | None
    target_component: dict[str, Any] | None

    @property
    def source_kind(self) -> str:
        return str((self.source_component or {}).get("kind") or "")

    @property
    def target_kind(self) -> str:
        return str((self.target_component or {}).get("kind") or "")


def _route_ports(ends: _WireEnds, source_port: str, target_port: str) -> tuple[str, str]:
    if ends.source_resident or ends.source_kind in {"event_source", "filter", "reducer"}:
        source_port = source_port or "output"
        if source_port != "output":
            raise ValueError("Route 必须来自 output 出口")
    elif ends.source_kind == "selector":
        branch_id = lineage_primitives.selector_branch_id_from_port(source_port)
        branches = lineage_primitives.normalize_selector_branches(
            authoring_component_config(ends.source_component).get("branches")
        )
        if branch_id not in {branch["id"] for branch in branches}:
            raise ValueError("Selector Route 必须来自已声明的 branch:<id> 出口")
    elif ends.source_kind == "loop":
        source_port = source_port or "continue"
        if source_port not in LOOP_OUTPUT_PORTS:
            raise ValueError("循环组件的出口只能是 continue 或 stop")
    else:
        raise ValueError("Route 的来源必须具有包输出端口")
    if not (ends.target_resident or ends.target_kind in _INVOKE_TARGET_KINDS):
        raise ValueError("Route 的目标必须具有输入端口")
    target_port = target_port or "invoke"
    allowed = LOOP_INPUT_PORTS if ends.target_kind == "loop" else {"invoke"}
    if target_port not in allowed:
        raise ValueError(f"Route 的目标端口无效：{target_port}")
    return source_port, target_port


def _watch_ports(ends: _WireEnds, source_port: str, target_port: str) -> tuple[str, str]:
    if ends.source_kind != "event_source":
        raise ValueError("watch wire 的来源必须是 Event Source")
    if not (
        ends.target_resident
        or ends.target_component
        or _RESIDENT_ID_RE.fullmatch(ends.target)
        or _WIRE_ID_RE.fullmatch(ends.target)
        or _HANDOFF_ID_RE.fullmatch(ends.target)
    ):
        raise ValueError("watch wire 的目标必须是 Resident、组件、连线或工作区交接")
    source_port = source_port or "watch"
    if source_port != "watch" or target_port:
        raise ValueError("Watch 从 Event Source 的 watch 端点直接绑定观察对象，不使用目标端口")
    return source_port, target_port


def _target_ports(ends: _WireEnds, source_port: str, target_port: str) -> tuple[str, str]:
    if ends.source_kind != "effect":
        raise ValueError("target wire 的来源必须是 Effect")
    endpoints = list(authoring_component_config(ends.source_component).get("endpoints") or [])
    if not endpoints:
        raise ValueError("这个 Effect 没有声明具名端点")
    source_port = source_port or (endpoints[0] if len(endpoints) == 1 else "")
    if source_port not in endpoints:
        raise ValueError(f"Effect 没有名为 {source_port or '（空）'} 的端点")
    if not ends.target_resident and not ends.target_component and not _RESIDENT_ID_RE.fullmatch(ends.target):
        raise ValueError("target wire 只能指向当前 HATG 的 Resident 或组件")
    if target_port:
        raise ValueError("Target 从 Effect 的具名端点直接绑定对象，不使用目标端口")
    return source_port, target_port


# Which ports each wire kind connects, and the default port of each end.
_WIRE_PORT_RULES: dict[str, Callable[[_WireEnds, str, str], tuple[str, str]]] = {
    "route": _route_ports,
    "watch": _watch_ports,
    "target": _target_ports,
}


def _route_delivery_config(config: dict[str, Any], ends: _WireEnds) -> dict[str, Any]:
    """How a Route delivers: envelope, invocation profiles, queueing and limits."""

    _reject_unknown_fields(config, _ROUTE_CONFIG_FIELDS, "route wire config")
    result: dict[str, Any] = {}
    if not _boolean(config.get("include_envelope"), True):
        result["include_envelope"] = False
    profiles = normalize_invoke_profiles(config.get("invoke_profiles"))
    if profiles:
        result["invoke_profiles"] = profiles
    unavailable_policy = (
        _text(config.get("unavailable_policy"), 24).lower() or DEFAULT_EVENT_UNAVAILABLE_POLICY
    )
    if unavailable_policy not in EVENT_UNAVAILABLE_POLICIES:
        raise ValueError("Invoke wire 的不可消费策略必须是 drop、latest 或 queue")
    result["unavailable_policy"] = unavailable_policy
    result["expires_after_seconds"] = max(
        0, min(MAX_EVENT_EXPIRES_AFTER_SECONDS, _integer(config.get("expires_after_seconds"), 0)),
    )
    if unavailable_policy == "queue":
        result["queue_limit"] = max(
            1, min(MAX_EVENT_QUEUE_LIMIT, _integer(config.get("queue_limit"), DEFAULT_EVENT_QUEUE_LIMIT)),
        )
    maximum = normalize_optional_limit(config.get("max_messages"))
    if maximum is not None:
        if not ends.target_resident:
            raise ValueError("发送消息次数上限只适用于指向 Resident 的 Route")
        result["max_messages"] = maximum
    # How the target Resident runs a delivery (decision_481dc4e6292c):
    # "session" keeps time and file work in the Resident's session.
    context_policy = _text(config.get("context_policy"), 16).lower()
    if context_policy:
        if context_policy != "session":
            raise ValueError('Invoke wire 的 context_policy 只能是 "session"')
        result["context_policy"] = context_policy
    # An Effect that needs no ordering (a Family Router) takes each
    # Event as it comes, several at once; Residents keep their order.
    if _boolean(config.get("parallel"), False):
        if ends.target_kind != "effect":
            raise ValueError("只有指向 Effect 的 Invoke wire 可以并行投递")
        result["parallel"] = True
    return result


def _normalize_wire(
    raw: object,
    *,
    residents: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    # Other extra request fields are dropped; the removed owner field is refused.
    if "function_block_instance_id" in item:
        raise ValueError("wire 不接受字段：function_block_instance_id")
    wire_id = _text(item.get("id"), 110).lower()
    if not _WIRE_ID_RE.fullmatch(wire_id):
        raise ValueError("Lineage 原语 wire id 无效")
    kind = _text(item.get("kind"), 32).lower()
    if kind not in PROGRAM_WIRE_KINDS:
        raise ValueError("Lineage 原语 wire kind 无效")
    source = _text(item.get("source"), 110).lower()
    target = _text(item.get("target"), 110).lower()
    if not source or not target or source == target:
        raise ValueError("Lineage 原语 wire 必须连接两个不同端点")
    ends = _WireEnds(
        source, target,
        residents.get(source), residents.get(target),
        components.get(source), components.get(target),
    )
    source_port, target_port = _WIRE_PORT_RULES[kind](
        ends, _text(item.get("source_port"), 80), _text(item.get("target_port"), 80),
    )
    result: dict[str, Any] = {
        "id": wire_id,
        "kind": kind,
        "source": source,
        "target": target,
        **_client_key(item),
    }
    label = _text(item.get("label"), MAX_TITLE_CHARS)
    if label:
        result["label"] = label
    result["source_port"] = source_port
    if target_port:
        result["target_port"] = target_port
    if kind == "watch":
        return result
    config = item.get("config") if isinstance(item.get("config"), dict) else {}
    if kind == "target":
        # A Target binds its object directly and has no delivery settings.
        _reject_unknown_fields(config, (), "target wire config")
        return result
    result["config"] = _route_delivery_config(config, ends)
    return result


def empty_program() -> dict[str, Any]:
    return {
        "schema": PROGRAM_SCHEMA,
        "components": [],
        "wires": [],
    }


def _effect_endpoint_names(program: dict[str, Any], component_id: str) -> list[str]:
    component = next(
        (
            item for item in program.get("components") or []
            if isinstance(item, dict) and str(item.get("id") or "") == component_id
        ),
        {},
    )
    config = component.get("config") if isinstance(component.get("config"), dict) else {}
    return [str(name) for name in config.get("endpoints") or []]


def reconcile_effect_endpoints(program: dict[str, Any], component_id: str) -> dict[str, list[str]]:
    """Drop Target wires whose endpoint the Effect no longer declares."""

    allowed = set(_effect_endpoint_names(program, component_id))
    kept: list[Any] = []
    removed: list[str] = []
    for raw_wire in program.get("wires") or []:
        wire = raw_wire if isinstance(raw_wire, dict) else {}
        if (
            str(wire.get("source") or "") == component_id
            and _text(wire.get("kind"), 32).lower() == "target"
            and (_text(wire.get("source_port"), 80) or "") not in allowed
        ):
            removed.append(str(wire.get("id") or ""))
            continue
        kept.append(raw_wire)
    program["wires"] = kept
    return {"removed_wire_ids": removed}


def reconcile_effect_endpoint_bindings(
    program: dict[str, Any],
    component_id: str,
    endpoint_reference_ids: object,
    *,
    lineage_resident_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Apply authored resource IDs to an Effect's named Target wires.

    The component inspector and canvas ports are two authoring surfaces for
    the same typed wires. This function is their single topology authority:
    entering an ID updates or creates the corresponding wire, while clearing
    an ID removes it without inventing a second endpoint representation.
    """

    allowed_ports = tuple(_effect_endpoint_names(program, component_id))
    requested = (
        endpoint_reference_ids
        if isinstance(endpoint_reference_ids, dict)
        else {}
    )
    unknown_ports = sorted(
        str(port)
        for port, resident_id in requested.items()
        if str(resident_id or "").strip() and str(port) not in allowed_ports
    )
    if unknown_ports:
        raise ValueError("Effect 没有声明这些端点：" + "、".join(unknown_ports))

    resident_ids = {
        _resident_id(resident_id, field="HATG Resident")
        for resident_id in lineage_resident_ids
    }
    component_ids = {
        _text(component.get("id"), 110).lower()
        for component in program.get("components") or []
        if isinstance(component, dict)
    }
    reference_ids = (resident_ids | component_ids) - {component_id}
    desired: dict[str, str] = {}
    for port in allowed_ports:
        reference_id = _text(requested.get(port), 110).lower()
        if not reference_id:
            desired[port] = ""
            continue
        if (
            reference_id not in reference_ids
            and not _RESIDENT_ID_RE.fullmatch(reference_id)
        ):
            raise ValueError(
                f"Effect {port} Reference 不在当前 HATG"
            )
        desired[port] = reference_id

    removed: list[str] = []
    updated: list[str] = []
    created: list[str] = []
    occupied_ports: set[str] = set()
    reconciled: list[dict[str, Any]] = []
    for raw_wire in program.get("wires") or []:
        wire = raw_wire if isinstance(raw_wire, dict) else {}
        wire_kind = _text(wire.get("kind"), 32).lower()
        if (
            str(wire.get("source") or "") != component_id
            or wire_kind != "target"
        ):
            reconciled.append(raw_wire)
            continue

        wire_id = str(wire.get("id") or "")
        source_port = _text(wire.get("source_port"), 80) or "target"
        reference_id = desired.get(source_port, "")
        if (
            source_port not in allowed_ports
            or not reference_id
            or source_port in occupied_ports
        ):
            removed.append(wire_id)
            continue
        changed = (
            str(wire.get("source_port") or "") != source_port
            or str(wire.get("target") or "") != reference_id
            or "target_port" in wire
        )
        wire["kind"] = "target"
        wire["source_port"] = source_port
        wire["target"] = reference_id
        wire.pop("target_port", None)
        occupied_ports.add(source_port)
        reconciled.append(raw_wire)
        if changed:
            updated.append(wire_id)

    for port in allowed_ports:
        reference_id = desired.get(port, "")
        if not reference_id or port in occupied_ports:
            continue
        # Reconnecting a removed endpoint creates a new graph object.
        wire_id = _item_id(None, "wire")
        reconciled.append(
            {
                "id": wire_id,
                "kind": "target",
                "source": component_id,
                "source_port": port,
                "target": reference_id,
            }
        )
        occupied_ports.add(port)
        created.append(wire_id)

    program["wires"] = reconciled
    return {
        "created_wire_ids": created,
        "updated_wire_ids": updated,
        "removed_wire_ids": removed,
    }


def reconcile_event_source_watch_binding(
    program: dict[str, Any],
    component_id: str,
    watch_subject: object,
    *,
    lineage_resident_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Reconcile the Event Source inspector with its canonical Watch wires.

    ``{"kind": "resident", "ids": [...]}`` sets several watched Residents at
    once for a type that watches many; otherwise one ``kind`` and ``id``.
    """

    requested = watch_subject if isinstance(watch_subject, dict) else {}
    subject_kind = _text(requested.get("kind"), 24).lower()
    if subject_kind in {"resident", "effect"} and isinstance(requested.get("ids"), list):
        return _reconcile_event_source_watches(
            program, component_id, requested["ids"], lineage_resident_ids=lineage_resident_ids, subject_kind=subject_kind,
        )
    subject_id = _text(requested.get("id"), 110).lower()
    if bool(subject_kind) != bool(subject_id):
        raise ValueError("Watch Subject 必须同时提供 kind 与 id")
    if subject_kind and subject_kind not in {
        "resident",
        "event_source",
        "effect",
        "wire",
    }:
        raise ValueError("Watch Subject kind 无效")

    components = {
        str(component.get("id") or ""): component
        for component in program.get("components") or []
        if isinstance(component, dict)
    }
    target = components.get(component_id) or {}
    if target.get("kind") != "event_source":
        raise ValueError("Watch 只能绑定到 Event Source")
    residents = {
        _text(value, 80).lower() for value in lineage_resident_ids
    }
    wires = [
        wire
        for wire in program.get("wires") or []
        if isinstance(wire, dict)
    ]
    wires_by_id = {
        str(wire.get("id") or ""): wire for wire in wires
    }
    if (
        subject_kind == "resident"
        and subject_id not in residents
        and not _RESIDENT_ID_RE.fullmatch(subject_id)
    ):
        raise ValueError("Watch Resident 不在当前 HATG")
    if subject_kind in {"event_source", "effect"}:
        subject = components.get(subject_id) or {}
        if subject.get("kind") != subject_kind:
            raise ValueError(f"Watch {subject_kind} 不在当前 HATG")
    if subject_kind == "wire" and subject_id not in wires_by_id:
        raise ValueError("Watch wire 不在当前 HATG")

    current = [
        wire
        for wire in wires
        if wire.get("kind") == "watch"
        and str(wire.get("source") or "") == component_id
    ]
    kept = [wire for wire in wires if wire not in current]
    removed = [str(wire.get("id") or "") for wire in current]
    created: list[str] = []
    updated: list[str] = []
    if subject_id:
        reusable = current[0] if current else None
        wire_id = (
            str(reusable.get("id") or "")
            if reusable
            else _stable_id(
                "wire",
                "watch-subject",
                subject_id,
                component_id,
            )
        )
        watch_wire: dict[str, Any] = {
            "id": wire_id,
            "kind": "watch",
            "target": subject_id,
            "source_port": "watch",
            "source": component_id,
        }
        kept.append(watch_wire)
        if reusable:
            removed.remove(wire_id)
            updated.append(wire_id)
        else:
            created.append(wire_id)
    program["wires"] = kept
    return {
        "created_wire_ids": created,
        "updated_wire_ids": updated,
        "removed_wire_ids": removed,
    }


def _reconcile_event_source_watches(
    program: dict[str, Any],
    component_id: str,
    requested_ids: list[Any],
    *,
    subject_kind: str = "resident",
    lineage_resident_ids: Iterable[str],
) -> dict[str, list[str]]:
    """Make the Event Source watch exactly ``requested_ids``; existing wires are kept by watched subject."""

    subject_ids = list(dict.fromkeys(
        _text(value, 110).lower() for value in requested_ids if _text(value, 110)
    ))
    residents = {_text(value, 80).lower() for value in lineage_resident_ids}
    if subject_kind == "effect":
        residents = {str(c.get("id")) for c in program.get("components", []) if c.get("kind") == "effect"}
    for subject_id in subject_ids:
        if subject_kind == "effect" and subject_id not in residents:
            raise ValueError("Watch Effect 不在当前 HATG")
        if subject_id not in residents and not _RESIDENT_ID_RE.fullmatch(subject_id):
            raise ValueError("Watch Resident 不在当前 HATG")
    target = next((
        component for component in program.get("components") or []
        if isinstance(component, dict) and str(component.get("id") or "") == component_id
    ), {})
    if target.get("kind") != "event_source":
        raise ValueError("Watch 只能绑定到 Event Source")
    event_type = authoring_event_type(target)
    if subject_kind == "resident" and subject_ids and _component_resident_scope(target) != "watched":
        raise ValueError(
            "监听范围是本 Lineage 或 Portal 全部 Resident 的 Event Source 不使用 Watch 连线"
        )
    if len(subject_ids) > 1 and event_type not in EVENT_SOURCE_MULTI_RESIDENT_TYPES:
        raise ValueError(f"{event_type} Event Source 只能监听一个 Resident")
    wires = [wire for wire in program.get("wires") or [] if isinstance(wire, dict)]
    current = [
        wire for wire in wires
        if wire.get("kind") == "watch" and str(wire.get("source") or "") == component_id
    ]
    by_subject = {str(wire.get("target") or ""): wire for wire in current}
    kept = [wire for wire in wires if wire not in current]
    created: list[str] = []
    updated: list[str] = []
    for subject_id in subject_ids:
        reusable = by_subject.get(subject_id)
        if reusable is not None:
            kept.append(reusable)
            updated.append(str(reusable.get("id") or ""))
            continue
        wire_id = _stable_id("wire", "watch-subject", subject_id, component_id)
        watch_wire: dict[str, Any] = {
            "id": wire_id,
            "kind": "watch",
            "target": subject_id,
            "source_port": "watch",
            "source": component_id,
        }
        kept.append(watch_wire)
        created.append(wire_id)
    program["wires"] = kept
    return {
        "created_wire_ids": created,
        "updated_wire_ids": updated,
        "removed_wire_ids": [
            str(wire.get("id") or "") for wire in current
            if str(wire.get("target") or "") not in subject_ids
        ],
    }


def _program_residents(lineage_nodes: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    nodes = [
        node for node in lineage_nodes
        if isinstance(node, dict) and _text(node.get("id"), 80)
    ]
    return {
        _resident_id(node.get("id"), field="HATG node"): node
        for node in nodes
    }


def _authored_items(item: dict[str, Any], before: dict[str, Any], name: str) -> list[Any]:
    """The authored collection, else the previous one, as a private copy."""

    return deepcopy(
        item.get(name)
        if isinstance(item.get(name), list)
        else before.get(name)
        if isinstance(before.get(name), list)
        else []
    )


def _require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(message)


def _check_operator_routes_acyclic(
    components_by_id: dict[str, dict[str, Any]],
    wires: list[dict[str, Any]],
) -> None:
    operator_ids = {
        component_id
        for component_id, component in components_by_id.items()
        if component.get("kind") in PACKET_OPERATOR_KINDS
    }
    dependencies: dict[str, set[str]] = {operator_id: set() for operator_id in operator_ids}
    for wire in wires:
        if (
            wire["kind"] == "route"
            and wire["source"] in operator_ids
            and wire["target"] in operator_ids
        ):
            dependencies[wire["source"]].add(wire["target"])
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(operator_id: str) -> None:
        if operator_id in visiting:
            raise ValueError("Packet Operator 的 Packet 路由不能形成循环")
        if operator_id in visited:
            return
        visiting.add(operator_id)
        for target_id in dependencies.get(operator_id, set()):
            visit(target_id)
        visiting.remove(operator_id)
        visited.add(operator_id)

    for operator_id in sorted(operator_ids):
        visit(operator_id)


def _watch_subject(
    event_type: str,
    subject_id: str,
    *,
    watches_resident: bool,
    components_by_id: dict[str, dict[str, Any]],
    wires_by_id: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """The runtime subject field the first Watch wire of an Event Source sets."""

    if watches_resident:
        if event_type not in RESIDENT_SUBJECT_EVENT_TYPES:
            raise ValueError(f"{event_type} Event Source 不能观察 Resident")
        return {"subject_resident_id": subject_id}
    if subject_id in components_by_id:
        subject_kind = components_by_id[subject_id].get("kind")
        if subject_kind == "effect":
            if event_type not in EVENT_SOURCE_EFFECT_OBSERVER_TYPES:
                raise ValueError(f"{event_type} Event Source 不能观察 Effect")
            return {"subject_effect_id": subject_id}
        if subject_kind == "event_source":
            if event_type not in EVENT_SOURCE_OBSERVER_TYPES:
                raise ValueError(f"{event_type} Event Source 不能观察 Event Source")
            return {"subject_event_source_id": subject_id}
        raise ValueError("Watch Subject 原语类型无效")
    if _HANDOFF_ID_RE.fullmatch(subject_id):
        if event_type not in EVENT_SOURCE_HANDOFF_OBSERVER_TYPES:
            raise ValueError(f"{event_type} Event Source 不能观察工作区交接")
        return {"subject_handoff_id": subject_id}
    subject_wire = wires_by_id.get(subject_id)
    if (
        event_type not in EVENT_SOURCE_DELIVERY_OBSERVER_TYPES
        or not subject_wire
        or subject_wire.get("kind") != "route"
    ):
        raise ValueError("投递运行 Event 只能 Watch 进入 Invoke 的 Route 连线")
    return {"subject_wire_id": subject_id}


def _check_additional_watch(
    event_type: str,
    subject_id: str,
    first_subject: dict[str, str],
    watched: set[str],
    *,
    watches_resident: bool,
    components_by_id: dict[str, dict[str, Any]],
) -> None:
    """Several Residents (or Effects) may share one Event Source of a type that watches many."""

    many = event_type in EVENT_SOURCE_MULTI_RESIDENT_TYPES
    if (
        many
        and (components_by_id.get(subject_id) or {}).get("kind") == "effect"
        and "subject_effect_id" in first_subject
    ):
        duplicate = "同一个 Effect 不能重复连接同一个事件源"
    elif many and watches_resident and "subject_resident_id" in first_subject:
        duplicate = "同一个 Resident 不能重复连到同一个 Event Source"
    else:
        raise ValueError(
            "每个 Event Source 只能有一个 Watch Subject；"
            "只有 Resident 类 Event 可以同时监听多个 Resident"
        )
    if subject_id in watched:
        raise ValueError(duplicate)
    watched.add(subject_id)


def _watch_subjects(
    components_by_id: dict[str, dict[str, Any]],
    wires: list[dict[str, Any]],
    wires_by_id: dict[str, dict[str, Any]],
    residents: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Check every Watch wire against its Event Source; each source's first subject."""

    subjects: dict[str, dict[str, str]] = {}
    watched: dict[str, set[str]] = {}
    for wire in wires:
        if wire["kind"] != "watch":
            continue
        observer_id = str(wire["source"])
        observer = components_by_id.get(observer_id) or {}
        event_type = authoring_event_type(observer)
        if _component_resident_scope(observer) != "watched":
            raise ValueError(
                "监听范围是本 Lineage 或 Portal 全部 Resident 的 Event Source "
                "不使用 Watch 连线；请删除由它发出的 Watch 连线，或把监听范围改回 Watch 连线指定"
            )
        subject_id = str(wire["target"])
        watches_resident = bool(
            subject_id in residents or _RESIDENT_ID_RE.fullmatch(subject_id)
        )
        if observer_id in subjects:
            _check_additional_watch(
                event_type, subject_id, subjects[observer_id], watched[observer_id],
                watches_resident=watches_resident, components_by_id=components_by_id,
            )
            continue
        subjects[observer_id] = _watch_subject(
            event_type, subject_id, watches_resident=watches_resident,
            components_by_id=components_by_id, wires_by_id=wires_by_id,
        )
        watched[observer_id] = {subject_id}
    return subjects


def _check_observer_dependencies(
    components: list[dict[str, Any]],
    wires_by_id: dict[str, dict[str, Any]],
    subjects: dict[str, dict[str, str]],
) -> None:
    """A run Event Source must not observe itself, directly or through other sources."""

    observed_by: dict[str, str] = {}
    for component in components:
        if (
            component.get("kind") != "event_source"
            or authoring_event_type(component) not in EVENT_SOURCE_OBSERVER_TYPES
        ):
            continue
        component_id = str(component["id"])
        subject = subjects.get(component_id) or {}
        observed_id = str(subject.get("subject_event_source_id") or "")
        if subject.get("subject_wire_id"):
            observed_id = str(wires_by_id[subject["subject_wire_id"]]["source"])
        if observed_id == component_id:
            raise ValueError("运行 Event Source 不能监听自身")
        if observed_id:
            observed_by[component_id] = observed_id
    for watcher_id in observed_by:
        seen: set[str] = set()
        current_id = watcher_id
        while current_id in observed_by:
            if current_id in seen:
                raise ValueError("运行 Event Source 监听关系不能形成循环")
            seen.add(current_id)
            current_id = observed_by[current_id]


_PROGRAM_FIELDS = frozenset({"schema", "components", "wires"})


def normalize_program(
    raw: object,
    *,
    lineage_nodes: Iterable[dict[str, Any]],
    previous: object = None,
) -> dict[str, Any]:
    """Normalize a persistable, partially wired HATG program.

    This layer validates graph identity and typed endpoints. It deliberately
    permits disconnected components so drag/drop authoring can be incremental;
    executable diagnostics are produced by :func:`program_diagnostics`.
    """

    item = raw if isinstance(raw, dict) else {}
    _reject_unknown_fields(item, _PROGRAM_FIELDS, "Lineage program")
    before = previous if isinstance(previous, dict) else {}
    residents = _program_residents(lineage_nodes)
    components_raw = _authored_items(item, before, "components")
    wires_raw = _authored_items(item, before, "wires")
    components = [
        _normalize_component(raw_component, residents)
        for raw_component in components_raw
    ]
    component_ids = [component["id"] for component in components]
    _require_unique(component_ids, "Lineage 程序 component id 不能重复")
    if set(component_ids) & set(residents):
        raise ValueError("Lineage 程序 component id 不能与 Resident id 冲突")
    components_by_id = {component["id"]: component for component in components}
    wires = [
        _normalize_wire(raw_wire, residents=residents, components=components_by_id)
        for raw_wire in wires_raw
    ]
    _require_unique([wire["id"] for wire in wires], "Lineage 程序 wire id 不能重复")
    _require_unique(
        [
            str(item.get("client_key") or "")
            for item in [*components, *wires]
            if str(item.get("client_key") or "")
        ],
        "Lineage 程序 client_key 不能重复",
    )
    _require_unique(
        [
            (wire["kind"], wire["source"], wire["source_port"], wire["target"], wire.get("target_port", ""))
            for wire in wires
        ],
        "相同端点与类型的 Lineage 程序连线不能重复",
    )
    _check_operator_routes_acyclic(components_by_id, wires)
    wires_by_id = {str(wire["id"]): wire for wire in wires}
    subjects = _watch_subjects(components_by_id, wires, wires_by_id, residents)
    _check_observer_dependencies(components, wires_by_id, subjects)
    _require_unique(
        [(str(wire["source"]), str(wire["source_port"])) for wire in wires if wire["kind"] == "target"],
        "每个 Effect 的一个具名 Endpoint 只能连接一个 Reference",
    )
    return {
        "schema": PROGRAM_SCHEMA,
        "components": components,
        "wires": wires,
    }


def _effect_endpoint_routes(
    effect_id: str,
    wires: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    return {
        str(wire.get("source_port") or ""): {
            "wire_id": str(wire["id"]),
            "target_id": str(wire["target"]),
        }
        for wire in wires
        if wire["kind"] == "target" and wire["source"] == effect_id
    }


def _event_source_watch_projection(
    event_source_id: str,
    wires: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Project the canonical Watch relation into the runtime event contract.

    Several Watch wires make ``subject_resident_ids`` or ``subject_effect_ids``;
    the Event Source fires for any one of the watched subjects.
    """

    watches = [
        wire
        for wire in wires
        if wire.get("kind") == "watch"
        and str(wire.get("source") or "") == event_source_id
    ]
    if not watches:
        return {}
    if len(watches) > 1:
        ordered = sorted(watches, key=lambda wire: str(wire.get("target") or ""))
        effect_watch = (components.get(str(ordered[0].get("target"))) or {}).get("kind") == "effect"
        return {
            ("subject_effect_ids" if effect_watch else "subject_resident_ids"): [str(wire.get("target") or "") for wire in ordered],
            **({"subject_effect_id": str(ordered[0].get("target"))} if effect_watch else {}),
            "watch_wire_id": str(ordered[0].get("id") or ""),
            "watch_wire_ids": [str(wire.get("id") or "") for wire in ordered],
        }
    watch = watches[0]
    subject_id = str(watch.get("target") or "")
    subject_component = components.get(subject_id) or {}
    if subject_id.startswith("resident_"):
        key = "subject_resident_id"
    elif subject_component.get("kind") == "effect":
        key = "subject_effect_id"
    elif subject_component.get("kind") == "event_source":
        key = "subject_event_source_id"
    elif _HANDOFF_ID_RE.fullmatch(subject_id):
        key = "subject_handoff_id"
    else:
        key = "subject_wire_id"
    return {
        key: subject_id,
        "watch_wire_id": str(watch.get("id") or ""),
    }


def loop_source_id(loop_id: str, port: str) -> str:
    """Dispatch source that carries a loop's continue or stop output."""

    return f"{loop_id}.{port}"


def event_source_id_for_wire(
    wire: dict[str, Any],
    components: dict[str, dict[str, Any]],
) -> str:
    """Return the dispatch source that feeds an Event wire."""

    source = str(wire.get("source") or "")
    if str((components.get(source) or {}).get("kind") or "") == "loop":
        return loop_source_id(source, str(wire.get("source_port") or "continue"))
    return source


# Plans by the content they were compiled from. Nearly every Program step
# compiles the definition it just read, and that definition rarely changes;
# compiling a large Lineage costs about ten times its fingerprint. The plan is
# shared, so callers must not change it; it is compiled from a private copy, so
# changing the inputs afterwards cannot reach it.
# Every live plan together took 8.2 MB on 2026-10-19 (130 Lineages); the limit
# leaves room for each Lineage's previous and current version.
_COMPILED_PLANS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_COMPILED_PLAN_LIMIT = 512
_COMPILED_PLANS_LOCK = threading.Lock()
# All a plan reads from a node. Status, timestamps and the last execution
# change on every member run and must not make the plan miss.
_COMPILED_NODE_FIELDS = ("id", "operator_executor")


def compile_program(
    program: object,
    nodes: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """The read-only execution plan of a program and its Lineage nodes."""
    node_list = [
        {field: node[field] for field in _COMPILED_NODE_FIELDS if field in node}
        for node in nodes if isinstance(node, dict)
    ]
    key = hashlib.sha256(json.dumps(
        [program, node_list], sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
    with _COMPILED_PLANS_LOCK:
        plan = _COMPILED_PLANS.get(key)
        if plan is not None:
            _COMPILED_PLANS.move_to_end(key)
            return plan
    plan = _compile_program(deepcopy(program), deepcopy(node_list))
    with _COMPILED_PLANS_LOCK:
        _COMPILED_PLANS[key] = plan
        while len(_COMPILED_PLANS) > _COMPILED_PLAN_LIMIT:
            _COMPILED_PLANS.popitem(last=False)
    return plan


def _compile_program(program: object, node_list: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_program(program, lineage_nodes=node_list)
    residents = {
        str(node.get("id") or ""): node for node in node_list
    }
    wires = normalized["wires"]
    components_by_id = {
        str(component["id"]): component
        for component in normalized["components"]
    }

    def incoming(target_id: str, kinds: set[str], port: str | None = None) -> list[str]:
        return sorted(
            str(wire["id"])
            for wire in wires
            if wire["target"] == target_id
            and wire["kind"] in kinds
            and (port is None or str(wire.get("target_port") or "invoke") == port)
        )

    effects: dict[str, dict[str, Any]] = {}
    loops: dict[str, dict[str, Any]] = {}
    for component in normalized["components"]:
        component_id = str(component["id"])
        if component["kind"] == "effect":
            config = authoring_component_config(component)
            routes = _effect_endpoint_routes(component_id, wires)
            effects[component_id] = {
                "effect_id": component_id,
                "title": str(component.get("title") or ""),
                "script": str(config.get("script") or ""),
                "endpoint_names": list(config.get("endpoints") or []),
                "endpoints": {name: route["target_id"] for name, route in routes.items()},
                "endpoint_routes": routes,
                "incoming_packet_wire_ids": incoming(component_id, {"route"}),

                "incoming_invocation_wire_ids": incoming(component_id, {"route"}),
            }
        elif component["kind"] == "loop":
            config = authoring_component_config(component)
            loops[component_id] = {
                "loop_id": component_id,
                "title": str(component.get("title") or ""),
                "max_rounds": int(config.get("max_rounds") or 1),
                "completion": str(config.get("completion") or "complete"),
                "round_wire_ids": incoming(component_id, {"route"}, "invoke"),
                "done_wire_ids": incoming(component_id, {"route"}, "done"),
                "continue_source_id": loop_source_id(component_id, "continue"),
                "stop_source_id": loop_source_id(component_id, "stop"),
            }
    packet_operators: dict[str, dict[str, Any]] = {}
    for component in normalized["components"]:
        if component.get("kind") not in PACKET_OPERATOR_KINDS:
            continue
        operator_id = str(component["id"])
        config = deepcopy(authoring_component_config(component))
        outgoing = [wire for wire in wires if wire["kind"] == "route" and wire["source"] == operator_id]
        packet_operators[operator_id] = {
            "operator_id": operator_id,
            "kind": str(component["kind"]),
            "title": str(component.get("title") or ""),
            "config": config,
            "resident_executor_id": str(config.get("resident_executor_id") or ""),
            "incoming_packet_wire_ids": incoming(operator_id, {"route"}),

            "outgoing_packet_wire_ids": sorted(str(wire["id"]) for wire in outgoing),
            "outgoing_packet_ports": {
                port: sorted(str(wire["id"]) for wire in outgoing if str(wire.get("source_port") or "") == port)
                for port in sorted({str(wire.get("source_port") or "") for wire in outgoing})
            },
            "effect_ids": sorted(
                {
                    str(wire["target"])
                    for wire in outgoing
                    if (components_by_id.get(str(wire["target"])) or {}).get("kind") == "effect"
                }
            ),
        }
    invoke_routes = {
        str(wire["id"]): {
            "wire_id": str(wire["id"]),
            "source_kind": str(wire["kind"]),
            "source_id": str(wire["source"]),
            "target_id": str(wire["target"]),
            "target_port": str(wire.get("target_port") or "invoke"),
            "delivery": deepcopy(wire.get("config") or {}),
            "invocation": compiled_invocation(
                wire,
                residents=residents,
                components=components_by_id,
            ),
        }
        for wire in wires
        if wire["kind"] in {"route"}
    }

    def route_entry(wire: dict[str, Any], source_port: str) -> dict[str, Any]:
        target_id = str(wire["target"])
        target_component = components_by_id.get(target_id)
        return {
            "wire_id": str(wire["id"]),
            "target_id": target_id,
            "target_kind": str(target_component.get("kind") or "") if target_component else "agent",
            "target_port": str(wire.get("target_port") or "invoke"),
            "config": deepcopy(wire.get("config") or {}),
            "invocation": deepcopy(invoke_routes[str(wire["id"])]["invocation"]),
            "source_port": source_port,
        }

    def route_summary(routes: list[dict[str, Any]]) -> dict[str, list[str]]:
        return {
            "subscriber_resident_ids": sorted(route["target_id"] for route in routes if route["target_kind"] == "agent"),
            "subscriber_operator_ids": sorted(
                route["target_id"] for route in routes if route["target_kind"] in PACKET_OPERATOR_KINDS
            ),
            "subscriber_effect_ids": sorted(route["target_id"] for route in routes if route["target_kind"] == "effect"),
            "subscriber_loop_ids": sorted(route["target_id"] for route in routes if route["target_kind"] == "loop"),
        }

    event_sources: dict[str, dict[str, Any]] = {}
    for component in normalized["components"]:
        component_id = str(component["id"])
        if component["kind"] == "event_source":
            event_type = authoring_event_type(component)
            runtime_config = deepcopy(authoring_component_config(component))
            runtime_config.pop("event_type", None)
            routes = [
                route_entry(wire, "output")
                for wire in wires
                if wire["kind"] == "route" and wire["source"] == component_id
            ]
            event_sources[component_id] = {
                **{key: deepcopy(value) for key, value in component.items() if key != "config"},
                **runtime_config,
                **_event_source_watch_projection(component_id, wires, components_by_id),
                "component_id": component_id,
                "source_port": "output",
                "event_type": event_type,
                "subscriber_routes": routes,
                **route_summary(routes),
            }
        elif component["kind"] == "loop":
            for port in sorted(LOOP_OUTPUT_PORTS):
                routes = [
                    route_entry(wire, port)
                    for wire in wires
                    if wire["kind"] == "route" and wire["source"] == component_id
                    and str(wire.get("source_port") or "continue") == port
                ]
                event_sources[loop_source_id(component_id, port)] = {
                    "id": component_id,
                    "kind": "loop",
                    "title": str(component.get("title") or ""),
                    "component_id": component_id,
                    "source_port": port,
                    "event_type": "",
                    "max_fires": None,
                    "subscriber_routes": routes,
                    **route_summary(routes),
                }
    return {
        "schema": PROGRAM_SCHEMA,
        "program": normalized,
        "event_sources": event_sources,
        "packet_operators": packet_operators,
        "operator_executors": {
            operator["resident_executor_id"]: operator_id
            for operator_id, operator in packet_operators.items()
            if operator.get("resident_executor_id")
        },
        "effects": effects,
        "loops": loops,
        "invoke_routes": invoke_routes,
        "wires": wires,
    }


# Effects calling DeliverMessage may leave ``message`` unset: the runtime then
# composes the message from the activation's input (decision_4adcc1ff2f2c).
EFFECT_RUNTIME_SUPPLIED_ARGUMENTS = {"DeliverMessage": frozenset({"message"})}


def _diagnostic(code: str, item_id: str, message: str, level: str = "error") -> dict[str, str]:
    return {"code": code, "item_id": item_id, "level": level, "message": message}


def effect_script_analysis(effect: dict[str, Any]) -> Any:
    """Check one compiled Effect's script against the instruction catalog."""

    from management_portal.hatg.catalog import instruction_catalog

    from .lineage_effect_script import analyze
    from .lineage_program_effect_rules import validate_effect_argument

    catalog = instruction_catalog()
    return analyze(
        str(effect.get("script") or ""),
        declaration_for=catalog.get,
        declared_endpoints=effect.get("endpoint_names") or [],
        connected_endpoints=list((effect.get("endpoints") or {}).keys()),
        endpoint_targets=dict(effect.get("endpoints") or {}),
        runtime_supplied=EFFECT_RUNTIME_SUPPLIED_ARGUMENTS,
        validate_argument=validate_effect_argument,
    )


def _effect_diagnostics(effect_id: str, effect: dict[str, Any]) -> list[dict[str, str]]:
    return [
        _diagnostic(code, effect_id, f"第 {line} 行：{message}" if line else message)
        for code, message, line in effect_script_analysis(effect).diagnostics
    ]


# Payload fields of Event types whose producers are fixed.  An Effect argument
# that reads another ``event.payload`` field from such a source receives
# nothing: a TAU run mapped ``event.payload.prompt`` from a
# resident.work_finished Event and the second stage failed at delivery.
DISPATCH_PAYLOAD_FIELDS = frozenset({
    "trigger_path_id", "route_path", "event_source_id", "source_event_id", "runtime_event_depth", "wire_id",
})
EVENT_PAYLOAD_FIELDS = {
    "portal.release_published": frozenset({"sequence", "publication_id", "commit", "previous_commit", "completed_at", "source"}),
    "script.finished": frozenset({"script_run_id", "status", "exit_code", "outputs", "path", "duration_seconds"}),
    "resident.work_finished": frozenset({"message_id", "execution_id"}),
    "resident.work_failed": frozenset({"message_id", "execution_id"}),
    "resident.work_cancelled": frozenset({"message_id", "execution_id"}),
    "residents.all_finished": frozenset({"resident_ids", "resident_count"}),
}


EVENT_PAYLOAD_REFERENCE_RE = re.compile(r"\$\{event\.payload\.([A-Za-z0-9_-]+)")


def _binding_sources(binding: object) -> set[str]:
    """Reference paths an argument mapping reads (``from`` and nested ``concat``)."""

    if not isinstance(binding, dict):
        return set()
    sources = {str(binding.get("from") or "").lstrip("$")} - {""}
    for part in binding.get("concat") or []:
        sources |= _binding_sources(part)
    return sources


def _effect_payload_diagnostics(
    effect_id: str,
    effect: dict[str, Any],
    event_sources: dict[str, Any],
    wires: dict[str, dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Arguments that read an Event payload field none of the Effect's Events carries."""

    event_types = set()
    for wire_id in effect.get("incoming_packet_wire_ids") or []:
        wire = wires.get(str(wire_id)) or {}
        source = event_sources.get(event_source_id_for_wire(wire, components)) or {}
        event_types.add(str(source.get("event_type") or ""))
    if not event_types or not event_types <= set(EVENT_PAYLOAD_FIELDS):
        return []
    available = DISPATCH_PAYLOAD_FIELDS.union(*(EVENT_PAYLOAD_FIELDS[name] for name in event_types))
    # Payload fields reach an Effect two ways: the script expands
    # ${event.payload.<field>}, or an activation wire maps one into arguments.
    readers: list[tuple[str, str]] = [
        ("脚本", f"event.payload.{match.group(1)}")
        for match in EVENT_PAYLOAD_REFERENCE_RE.finditer(str(effect.get("script") or ""))
    ]
    for wire_id in effect.get("incoming_packet_wire_ids") or []:
        wire = wires.get(str(wire_id)) or {}
        config = wire.get("config") if isinstance(wire.get("config"), dict) else {}
        profile = (config.get("invoke_profiles") or {}).get("effect") or {}
        for name, binding in sorted((profile.get("arguments") or {}).items()):
            if isinstance(binding, dict):
                readers.extend((f"参数 {name}", source) for source in _binding_sources(binding))
    result = []
    reported: set[tuple[str, str]] = set()
    for reader, source in readers:
        if not source.startswith("event.payload.") or (reader, source) in reported:
            continue
        reported.add((reader, source))
        field = source.split(".", 2)[2].split(".", 1)[0]
        if field not in available:
            result.append(_diagnostic(
                "effect.unavailable_event_payload", effect_id,
                f"{reader}读取 {source}，但上游 {'、'.join(sorted(event_types))} Event 不含 {field}；"
                f"可用字段：{'、'.join(sorted(available))}",
                "warning",
            ))
    return result


def _event_output_names(source: dict[str, Any], effects: dict[str, Any]) -> set[str] | None:
    """Outputs carried by a source's events: the fields an observed Effect's script outputs.

    ``None`` means the outputs cannot be known before the Lineage runs.
    """

    from .lineage_program_effect_rules import effect_failure_outputs

    event_type = str(source.get("event_type") or "")
    if event_type == "script.finished":
        return None
    if event_type not in EVENT_SOURCE_EFFECT_OBSERVER_TYPES:
        return set()
    if event_type.endswith("failed"):
        return set(effect_failure_outputs({}))
    effect = effects.get(str(source.get("subject_effect_id") or "")) or {}
    if not effect:
        return None
    return effect_script_analysis(effect).output_fields


def program_diagnostics(
    program: object,
    nodes: Iterable[dict[str, Any]],
    *,
    _compiled_plan: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Errors that stop a Lineage from running and warnings worth attention."""

    node_list = [node for node in nodes if isinstance(node, dict)]
    plan = _compiled_plan or compile_program(program, node_list)
    diagnostics: list[dict[str, str]] = []
    event_sources = plan.get("event_sources") or {}
    for source_id, source in event_sources.items():
        if not isinstance(source, dict) or source.get("kind") != "event_source":
            continue
        event_type = str(source.get("event_type") or "")
        if (
            event_type in RESIDENT_SUBJECT_EVENT_TYPES
            and event_source_resident_scope(source) == "watched"
            and not event_source_subject_ids(source)
        ):
            diagnostics.append(_diagnostic("event.missing_subject", source_id, "Event Source 尚未指定目标 Resident"))
        if event_type == "file.changed" and not source.get("path"):
            diagnostics.append(_diagnostic("event.missing_path", source_id, "file.changed Event Source 尚未指定文件"))
        if event_type == "time.reached" and str(source.get("schedule_mode") or "at") == "at" and not source.get("scheduled_at"):
            diagnostics.append(_diagnostic("event.missing_time", source_id, "time.reached Event Source 尚未指定时间"))
        if event_type == "budget.exhausted":
            subject_id = str(source.get("subject_event_source_id") or "")
            if not subject_id:
                diagnostics.append(_diagnostic(
                    "event.missing_event_source_subject", source_id, "budget.exhausted 尚未指定要观察的 Event Source",
                ))
            elif (event_sources.get(subject_id) or {}).get("max_fires") is None:
                diagnostics.append(_diagnostic(
                    "event.unbounded_budget_subject", source_id,
                    "被观察的 Event Source 没有设置最大触发次数，不会产生 budget.exhausted", "warning",
                ))
        elif event_type in EVENT_SOURCE_DELIVERY_OBSERVER_TYPES and not (
            source.get("subject_event_source_id") or source.get("subject_wire_id")
        ):
            diagnostics.append(_diagnostic(
                "event.missing_delivery_subject", source_id, "投递运行 Event Source 尚未指定要观察的 Event Source 或连线",
            ))
        if event_type in EVENT_SOURCE_EFFECT_OBSERVER_TYPES and not source.get("subject_effect_id"):
            diagnostics.append(_diagnostic("event.missing_effect_subject", source_id, f"{event_type} 尚未指定要观察的 Effect"))
        if event_type in EVENT_SOURCE_HANDOFF_OBSERVER_TYPES and not source.get("subject_handoff_id"):
            diagnostics.append(_diagnostic("event.missing_handoff_subject", source_id, f"{event_type} 尚未指定要观察的工作区交接"))
        if not source.get("subscriber_routes") and event_type not in EVENT_SOURCE_OBSERVER_TYPES:
            watched = any(
                (other or {}).get("subject_event_source_id") == source_id for other in event_sources.values()
            )
            if not watched:
                diagnostics.append(_diagnostic("event.unconnected", source_id, "Event Source 尚未连接任何目标", "warning"))
    effects = plan.get("effects") or {}
    components = {str(item.get("id") or ""): item for item in (plan.get("program") or {}).get("components") or []}
    wires_by_id = {str(wire.get("id") or ""): wire for wire in plan.get("wires") or []}
    for effect_id, effect in effects.items():
        diagnostics.extend(_effect_diagnostics(effect_id, effect))
        diagnostics.extend(_effect_payload_diagnostics(effect_id, effect, event_sources, wires_by_id, components))
    for wire in plan.get("wires") or []:
        route = (plan.get("invoke_routes") or {}).get(str(wire.get("id") or "")) or {}
        references = invocation_argument_output_reference_names((route.get("invocation") or {}).get("arguments"))
        if wire.get("kind") != "route" or not references:
            continue
        source = event_sources.get(event_source_id_for_wire(wire, components)) or {}
        available = _event_output_names(source, effects)
        if available is not None and not references <= available:
            diagnostics.append(_diagnostic(
                "wire.invalid_argument_mapping_output", str(wire.get("id") or ""),
                "参数映射引用了上游 Event 不提供的输出：" + "、".join(sorted(references - available)),
            ))
    for operator_id, operator in (plan.get("packet_operators") or {}).items():
        if (operator.get("config") or {}).get("operator_implementation") == "resident" and not operator.get("resident_executor_id"):
            diagnostics.append(_diagnostic(
                "packet_operator.missing_executor", operator_id, "Resident 式算子尚未绑定执行者",
            ))
        if operator.get("incoming_packet_wire_ids") and not operator.get(
            "outgoing_packet_wire_ids"
        ):
            diagnostics.append(_diagnostic(
                "packet_operator.missing_output", operator_id,
                f"{str(operator.get('kind') or 'Packet 算子')} 的输出尚未连接下游",
            ))
    for loop_id, loop in (plan.get("loops") or {}).items():
        if not loop.get("round_wire_ids"):
            diagnostics.append(_diagnostic("loop.missing_input", loop_id, "循环组件尚未连接轮次输入", "warning"))
        continue_routes = (event_sources.get(loop["continue_source_id"]) or {}).get("subscriber_routes") or []
        if loop.get("round_wire_ids") and not continue_routes:
            diagnostics.append(_diagnostic("loop.missing_output", loop_id, "循环组件的 continue 出口尚未连接下游"))
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        key = (diagnostic["code"], diagnostic["item_id"])
        if key not in seen:
            seen.add(key)
            unique.append(diagnostic)
    return unique

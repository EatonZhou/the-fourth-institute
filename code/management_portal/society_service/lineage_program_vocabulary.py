"""What a Lineage Program is made of: component and wire kinds, configuration fields,
limits and identifier patterns, and the accessors of a component's authored config.

A new kind, field or limit is declared here; the modules that normalize, compile and
check Programs read it from here.
"""

from __future__ import annotations

import re
from typing import Any

from . import lineage_primitive_definitions, lineage_primitives
from .lineage_values import _text


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
_FUNCTION_BLOCK_ID_RE = re.compile(r"^fbi_[0-9a-f]{12}$")
_LINEAGE_ID_RE = re.compile(r"^lineage_[0-9a-f]{12}$")
_RESIDENT_ID_RE = lineage_primitives.RESIDENT_ID_RE


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


_HANDOFF_ID_RE = re.compile(r"^handoff_[a-z0-9][a-z0-9_-]{2,95}$")

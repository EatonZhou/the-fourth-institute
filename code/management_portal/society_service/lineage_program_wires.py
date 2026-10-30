"""Normalizing one authored Program wire: what its ends are, which ports each wire kind
connects, and how a Route delivers.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple

from management_common.limit_contract import normalize_optional_limit
from . import lineage_primitives
from .lineage_program_arguments import normalize_invoke_profiles
from .lineage_program_fields import _boolean, _client_key, _reject_unknown_fields
from .lineage_program_vocabulary import (
    DEFAULT_EVENT_QUEUE_LIMIT, DEFAULT_EVENT_UNAVAILABLE_POLICY, EVENT_UNAVAILABLE_POLICIES,
    LOOP_INPUT_PORTS, LOOP_OUTPUT_PORTS, MAX_EVENT_EXPIRES_AFTER_SECONDS, MAX_EVENT_QUEUE_LIMIT,
    MAX_TITLE_CHARS, PACKET_OPERATOR_KINDS, PROGRAM_WIRE_KINDS, _HANDOFF_ID_RE, _RESIDENT_ID_RE,
    _WIRE_ID_RE, authoring_component_config,
)
from .lineage_values import _integer, _text


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

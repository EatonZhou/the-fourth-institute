"""Normalizing one authored Program component: Packet Operators, Effects, Seeds, Loops and
Event Sources, with the config steps of each Event type.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from management_common.limit_contract import normalize_optional_limit
from . import (
    lineage_graph_state, lineage_primitive_definitions, lineage_primitives,
    lineage_recurring_schedule,
)
from .lineage_program_event_scope import RESIDENT_SCOPES, SCOPED_RESIDENT_EVENT_TYPES
from .lineage_program_fields import (
    _boolean, _bounded_integer, _client_key, _iso_datetime, _percentage_threshold, _position,
    _reject_unknown_fields, _relative_path, _resident_id,
)
from .lineage_program_vocabulary import (
    DEFAULT_EVENT_SOURCE_TYPE, EFFECT_CONFIG_FIELDS, EFFECT_ENDPOINT_RE,
    EVENT_SOURCE_AUTHORING_CONFIG_FIELDS, EVENT_SOURCE_TYPES, LOOP_COMPLETIONS, LOOP_CONFIG_FIELDS,
    MAX_EFFECT_ENDPOINTS, MAX_EVENT_EXPIRES_AFTER_SECONDS, MAX_EVENT_FIRES, MAX_LOOP_ROUNDS,
    MAX_PATH_CHARS, MAX_TITLE_CHARS, PACKET_OPERATOR_CONFIG_FIELDS, PACKET_OPERATOR_IMPLEMENTATIONS,
    PACKET_OPERATOR_KINDS, PROGRAM_COMPONENT_KINDS, RULE_REDUCE_STRATEGIES, SEED_CONFIG_FIELDS,
    STATE_CONDITION_OPERATORS, _COMPONENT_ID_RE, _RESIDENT_ID_RE,
)
from .lineage_values import _integer, _text


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

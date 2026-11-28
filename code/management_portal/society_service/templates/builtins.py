"""Initial content for editable built-in templates."""

from __future__ import annotations

from ..lineage_effect_script import deliver_message_script
from .registry import template_id  # noqa: F401  (builtins' identities stay available here)


def _prompt(*parts):
    pieces = [part if isinstance(part, dict) else {"value": part} for part in parts]
    return {
        "invoke_profiles": {
            "agent": {
                "arguments": {
                    "prompt": pieces[0]
                    if len(pieces) == 1
                    else {"concat": pieces, "separator": "\n\n"}
                }
            }
        }
    }


def _wire(key, kind, source, target, **kwargs):
    return {"key": key, "kind": kind, "source": source, "target": target, **kwargs}


def _component(key, kind, title, config):
    return {"key": key, "kind": kind, "title": title, "config": config}


def _timer(interval):
    return {
        "event_type": "time.reached",
        "schedule_mode": "recurring",
        "recurrence_kind": "interval",
        "interval_seconds": interval,
        "misfire_policy": "coalesce_latest",
        "overlap_policy": "drop",
    }


def _effect(message):
    return {"script": deliver_message_script(message), "endpoints": ["target"]}


def _structure(roles, components, wires):
    return {
        "roles": roles,
        "components": components,
        "wires": wires,
        "function_block": True,
    }


def definitions():
    target = {"key": "target", "from": "target", "description": "被推进的 Resident"}
    event_prompt = {"from": "event.payload.prompt", "default": ""}

    def rule(operator):
        return {
            "operator_implementation": "rules",
            "selection": {
                "mode": "all",
                "conditions": [
                    {"from": "packet.message.text", "operator": operator, "value": "DONE"}
                ],
                "order_by": "input",
                "limit": None,
            },
        }

    from ..family_tasks import defaults
    review = defaults("notify_periodic_review")["review"]
    from . import role_recipes as role
    templates = [
        {
            "builtin_key": "family_review", "kind": "family_duty", "category": "structure",
            "title": "周期组织回顾", "description": "施加到普通 Family 的 Steward，独立管理回顾周期与指南，可编辑、撤下和再安装。",
            "targets": ["resident"], "parameters": [],
            "body": {"interval_seconds": 86400, "guide": review["guide"], "prompt": review["prompt"]},
        },
        {
            "builtin_key": "workshop_assistant",
            "kind": "structure", "category": "structure", "title": "工坊助手",
            "description": "在用户的 My Lineage 中创建该用户的模板工坊助手。",
            "targets": ["canvas"], "parameters": [],
            "body": {"roles": [{"key": "assistant", "from": "new", "title": "工坊助手"}],
                     "components": [], "wires": [], "function_block": True},
        },
        {
            "builtin_key": "family",
            "kind": "familize",
            "category": "team",
            "title": "Family",
            "description": "把选中的 Resident 组合为 Family，新建门面、steward 与 Router。",
            "targets": ["selection", "resident"],
            "parameters": [],
            "body": {
                "role_templates": role.references(),
                "goal": "",
                "route_threshold": 60,
                "review_interval_seconds": 86400,
            },
        },
        {
            "builtin_key": "agent_loop",
            "kind": "structure",
            "category": "structure",
            "title": "Agent Loop",
            "description": "目标完成一轮后由评审检查；未完成时把意见发回目标，DONE 或轮数上限结束循环。",
            "targets": ["resident"],
            "parameters": [
                {
                    "name": "role",
                    "type": "enum",
                    "options": ["critic", "questioner", "ideator", "supervisor"],
                    "default": "critic",
                    "description": "评审、提问、构思或监督职责",
                },
                {"name": "max_rounds", "type": "integer", "default": 5},
                {
                    "name": "task",
                    "type": "text",
                    "default": "检查目标 Resident 最近一轮的结果。按你的职责提出具体修改；目标完成时只回复 DONE。",
                },
            ],
            "body": _structure(
                [
                    target,
                    {
                        "key": "reviewer",
                        "from": "new",
                        "title": "{{role.target.title}} · {{param.role}}",
                        "provider": "codex",
                        "settings": {},
                    },
                ],
                [
                    _component(
                        "target_done",
                        "event_source",
                        "目标完成一轮",
                        {"event_type": "resident.work_finished"},
                    ),
                    _component(
                        "rounds",
                        "loop",
                        "轮数",
                        {"max_rounds": "{{param.max_rounds}}", "completion": "rearm"},
                    ),
                    _component("not_done", "filter", "未完成", rule("not_contains")),
                    _component("is_done", "filter", "已完成", rule("contains")),
                ],
                [
                    _wire("watch_target", "watch", "target_done", "target"),
                    _wire("count_round", "route", "target_done", "rounds"),
                    _wire(
                        "ask_reviewer",
                        "route",
                        "rounds",
                        "reviewer",
                        source_port="continue",
                        config=_prompt(
                            "你在循环中的职责是 {{param.role}}（critic 找问题，questioner 提问题，ideator 提想法，supervisor 督促完成）。",
                            "{{param.task}}",
                        ),
                    ),
                    _wire("review_open", "route", "reviewer", "not_done"),
                    _wire("review_done", "route", "reviewer", "is_done"),
                    _wire(
                        "send_back",
                        "route",
                        "not_done",
                        "target",
                        config=_prompt({"from": "packet.message.text", "default": ""}),
                    ),
                    _wire(
                        "finish", "route", "is_done", "rounds", target_port="done"
                    ),
                ],
            ),
        },
        {
            "builtin_key": "fixed_reminder",
            "kind": "structure",
            "category": "structure",
            "title": "固定提醒",
            "description": "目标每完成一轮就发送同一条提示词，由循环组件限制次数。",
            "targets": ["resident"],
            "parameters": [
                {
                    "name": "message",
                    "type": "text",
                    "default": "请继续推进当前任务，并说明本轮进展。",
                },
                {"name": "max_rounds", "type": "integer", "default": 5},
            ],
            "body": _structure(
                [target],
                [
                    _component(
                        "target_done",
                        "event_source",
                        "目标完成一轮",
                        {"event_type": "resident.work_finished"},
                    ),
                    _component(
                        "rounds",
                        "loop",
                        "轮数",
                        {"max_rounds": "{{param.max_rounds}}", "completion": "rearm"},
                    ),
                    _component(
                        "remind", "effect", "发送提醒", _effect("{{param.message}}")
                    ),
                ],
                [
                    _wire("watch_target", "watch", "target_done", "target"),
                    _wire("count_round", "route", "target_done", "rounds"),
                    _wire(
                        "fire",
                        "route",
                        "rounds",
                        "remind",
                        source_port="continue",
                        config={"include_envelope": False},
                    ),
                    _wire(
                        "remind_target",
                        "target",
                        "remind",
                        "target",
                        source_port="target",
                    ),
                ],
            ),
        },
        {
            "builtin_key": "scheduled_work",
            "kind": "structure",
            "category": "structure",
            "title": "定时工作",
            "description": "每天按系统本地时间推进目标 Resident，也可以从“运行一次”手动触发。",
            "targets": ["resident"],
            "parameters": [
                {
                    "name": "task",
                    "type": "text",
                    "required": True,
                    "description": "每次要做的工作",
                },
                {
                    "name": "calendar_time",
                    "type": "text",
                    "default": "09:00",
                    "required": True,
                    "description": "每天触发时间（24 小时制，如 09:00 或 21:30）",
                    "multiline": False,
                },
            ],
            "body": _structure(
                [target],
                [
                    _component(
                        "timer",
                        "event_source",
                        "定时",
                        {
                            "event_type": "time.reached",
                            "schedule_mode": "recurring",
                            "recurrence_kind": "calendar",
                            "calendar_frequency": "daily",
                            "calendar_time": "{{param.calendar_time}}",
                            "timezone": "local",
                            "jitter_seconds": 0,
                            "misfire_policy": "coalesce_latest",
                            "overlap_policy": "drop",
                        },
                    ),
                    _component(
                        "manual",
                        "event_source",
                        "运行一次",
                        {"event_type": "manual.triggered"},
                    ),
                ],
                [
                    _wire(
                        "scheduled",
                        "route",
                        "timer",
                        "target",
                        config=_prompt("{{param.task}}"),
                    ),
                    _wire(
                        "on_demand",
                        "route",
                        "manual",
                        "target",
                        config=_prompt("{{param.task}}", event_prompt),
                    ),
                ],
            ),
        },
        {
            "builtin_key": "leader_pulse",
            "kind": "structure",
            "category": "structure",
            "title": "Leader Pulse",
            "description": "周期性向 Lineage Leader 发送进展检查任务。",
            "targets": ["canvas"],
            "parameters": [
                {"name": "interval_seconds", "type": "duration", "default": 900},
                {
                    "name": "prompt",
                    "type": "text",
                    "default": "检查这个 Lineage 的节点状态、最近结果与 Workspace 变化，归纳阻塞、偏航与下一步协调事项，必要时给成员派活。",
                },
            ],
            "body": _structure(
                [{"key": "leader", "from": "lineage_leader"}],
                [
                    _component(
                        "pulse",
                        "event_source",
                        "周期检查",
                        _timer("{{param.interval_seconds}}"),
                    ),
                    _component(
                        "send", "effect", "发送检查任务", _effect("{{param.prompt}}")
                    ),
                ],
                [
                    _wire(
                        "check",
                        "route",
                        "pulse",
                        "send",
                        config={"include_envelope": False},
                    ),
                    _wire(
                        "leader_target",
                        "target",
                        "send",
                        "leader",
                        source_port="target",
                    ),
                ],
            ),
        },
        {
            "builtin_key": "revision_return",
            "kind": "prompt",
            "category": "apply",
            "title": "修订回流",
            "description": "向目标投递一次可编辑的修订提示词。",
            "targets": ["resident", "selection"],
            "parameters": [
                {
                    "name": "prompt",
                    "type": "text",
                    "default": "根据以下意见修订你的成果，并说明改了什么。",
                }
            ],
            "body": {"message": "{{param.prompt}}", "activation_policy": "queue"},
        },
    ]
    scope = "你是为这次探索 Fork 出来的 Resident：只做发散与比较，把结果写进指定的候选文件，不改动已有的工作成果。"
    for key, title, seed_type, task in (
        (
            "expand_ideas",
            "扩展想法",
            "idea",
            "请基于当前目标、已有材料和成果提出互有差异、可验证的候选想法。写清核心判断、所需证据、主要风险和最小验证步骤。",
        ),
        (
            "expand_plans",
            "扩展方案",
            "plan",
            "请把当前方向展开为可比较、可执行的方案。写清执行步骤、依赖、产物、验收条件和失败后的调整入口；最后给出基于证据的选择建议。",
        ),
    ):
        templates.append(
            {
                "builtin_key": key,
                "kind": "explore",
                "category": "apply",
                "title": title,
                "description": "Fork 一个探索者，在来源旁边保留候选 Seed；采纳候选时从来源 Fork。",
                "targets": ["resident"],
                "parameters": [
                    {
                        "name": "count",
                        "type": "integer",
                        "default": 3,
                        "description": "候选数量（1–12）",
                    },
                    {"name": "task", "type": "text", "default": task + scope},
                ],
                "body": {
                    "seed_type": seed_type,
                    "label": "候选想法" if seed_type == "idea" else "候选方案",
                    "count": "{{param.count}}",
                    "task": "{{param.task}}",
                },
            }
        )
    templates.extend({'builtin_key': 'family_role_' + key, 'kind': 'role', 'category': 'structure',
                      'title': key.title(), 'description': '所属 Family 的独立角色；共享对象通过外部绑定接入。',
                      'targets': ['resident'], 'parameters': [], 'body': role.definition(key)} for key in role.KEYS)
    descriptions = {
        'leader_pulse':'检查周期到期时，提醒 Lineage 负责人检查当前目标与工作进度',
        'fixed_reminder':'目标完成一轮且尚未达到轮数上限时，发送提醒以推进下一轮工作',
        'scheduled_work':'到达设定时间或用户手动触发时，把约定的工作交给目标 Resident',
    }
    for item in templates:
        if item['builtin_key'] in descriptions:
            item['body']['duty']={'description':descriptions[item['builtin_key']], 'category':'common'}
        if item['builtin_key']=='scheduled_work':
            next(p for p in item['parameters'] if p['name']=='calendar_time')['type']='time'
        item['title']={'agent_loop':'循环评审','leader_pulse':'定期检查进展'}.get(item['builtin_key'],item['title'])
    return [
        {**item, "template_id": template_id(item["builtin_key"])} for item in templates
    ]

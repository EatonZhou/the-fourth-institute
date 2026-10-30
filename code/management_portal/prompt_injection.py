"""Which Portal-added input a Resident's turns carry, and when.

A Resident's ``prompt_injection`` setting picks a preset or sets each
category's timing itself (decision_ef1d6c925955).  Where a category goes is
the Provider's: Portal places it, the user does not choose.  The ``pure``
preset sends nothing Portal adds, like the Provider's own TUI; the Provider's
own system prompt (Claude Code's preset) stays, as it does there.

Fork history, attached references and system-provided runtime context are
the content of a message, not presets, and always travel with it.  The Agent
Harness reminders have their own settings.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


TIMINGS = ("off", "session_start", "session_start_and_after_compaction", "every_turn")
ENVELOPE_CHOICES = ("off", "every_turn")
SKILL_CHOICES = ("off", "list", "with_rules")

CHOICE_LABELS = {
    "off": "关闭",
    "session_start": "首轮",
    "session_start_and_after_compaction": "首轮 + 压缩后",
    "every_turn": "每轮",
    "list": "只发清单",
    "with_rules": "连同核心规则重发",
}

# id -> (label, what it is, allowed choices), in the order the model gets them.
CATEGORIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "core_rules": (
        "Portal 核心规则",
        "Portal 核心 Skill 与 Resident 角色规则、提问卡片规范、子 agent 许可",
        TIMINGS,
    ),
    "resident_md": (
        "resident.md",
        "Resident 的长期身份说明",
        TIMINGS,
    ),
    "runtime_identity": (
        "运行身份",
        "resident_id、lineage_id 与以本 Resident 身份调用 HATG ISA 的说明",
        TIMINGS,
    ),
    "authorization_context": (
        "授权上下文",
        "本轮执行者、消息来源与工作目录参考",
        TIMINGS,
    ),
    "message_envelope": (
        "消息包装",
        "<user-message> 标签，带附件时的会话说明、发送者与 Resident 信息，以及门面发来消息的回复说明",
        ENVELOPE_CHOICES,
    ),
    "turn_skills": (
        "任务 Skill 清单",
        "某条消息额外要求读取的 Skill",
        SKILL_CHOICES,
    ),
}

PRESETS: dict[str, dict[str, str]] = {
    "standard": {
        "core_rules": "session_start_and_after_compaction",
        "resident_md": "session_start_and_after_compaction",
        "runtime_identity": "every_turn",
        "authorization_context": "every_turn",
        "message_envelope": "every_turn",
        "turn_skills": "list",
    },
    "full": {
        "core_rules": "every_turn",
        "resident_md": "every_turn",
        "runtime_identity": "every_turn",
        "authorization_context": "every_turn",
        "message_envelope": "every_turn",
        "turn_skills": "list",
    },
    "minimal": {
        "core_rules": "session_start",
        "resident_md": "session_start",
        "runtime_identity": "session_start",
        "authorization_context": "session_start",
        "message_envelope": "every_turn",
        "turn_skills": "list",
    },
    "pure": {
        "core_rules": "off",
        "resident_md": "off",
        "runtime_identity": "off",
        "authorization_context": "off",
        "message_envelope": "off",
        "turn_skills": "off",
    },
}
PRESET_LABELS = {
    "standard": "标准",
    "full": "每轮完整",
    "minimal": "精简",
    "pure": "纯 TUI",
    "custom": "自定义",
}
DEFAULT_PRESET = "standard"


def _clean_parts(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("prompt_injection.parts must be an object")
    unknown = sorted(set(value) - set(CATEGORIES))
    if unknown:
        raise ValueError("prompt_injection.parts 不接受类别：" + "、".join(unknown))
    parts: dict[str, str] = {}
    for name, raw in value.items():
        choice = str(raw or "").strip()
        allowed = CATEGORIES[name][2]
        if choice not in allowed:
            raise ValueError(f"prompt_injection.parts.{name} 只接受 " + "、".join(allowed))
        parts[name] = choice
    return parts


def _named(parts: Mapping[str, str]) -> str:
    return next((name for name, preset in PRESETS.items() if dict(parts) == preset), "custom")


def normalize(value: object) -> dict[str, Any]:
    """The stored form: ``{"preset": name}``, or ``custom`` with every part.

    Custom parts that equal a preset are stored as that preset.
    """
    if value is None:
        return {"preset": DEFAULT_PRESET}
    if not isinstance(value, Mapping):
        raise ValueError("prompt_injection must be an object")
    unknown = sorted(set(value) - {"preset", "parts"})
    if unknown:
        raise ValueError("prompt_injection 不接受字段：" + "、".join(unknown))
    preset = str(value.get("preset") or "").strip()
    parts = _clean_parts(value.get("parts"))
    if not preset:
        preset = "custom" if parts else DEFAULT_PRESET
    if preset not in PRESET_LABELS:
        raise ValueError("prompt_injection.preset 只接受 " + "、".join(PRESET_LABELS))
    if preset != "custom":
        if parts and {**PRESETS[preset], **parts} != PRESETS[preset]:
            raise ValueError("prompt_injection.parts 只能与 custom 预设一起修改")
        return {"preset": preset}
    full = {**PRESETS[DEFAULT_PRESET], **parts}
    named = _named(full)
    return {"preset": named} if named != "custom" else {"preset": "custom", "parts": full}


def resolve(value: object) -> dict[str, str]:
    """Every category's choice for a stored (or absent) setting."""
    clean = normalize(value)
    if clean["preset"] == "custom":
        return dict(clean["parts"])
    return dict(PRESETS[clean["preset"]])


def apply_changes(current: object, *, preset: str = "", parts: object = None) -> dict[str, Any]:
    """Change a setting: pick a preset, or change single categories.

    Categories change on top of the given preset, or of the current choices.
    """
    changes = _clean_parts(parts)
    name = str(preset or "").strip()
    if name and name not in PRESET_LABELS:
        raise ValueError("preset 只接受 " + "、".join(PRESET_LABELS))
    if not name and not changes:
        raise ValueError("需要 preset 或 parts")
    if name and name != "custom" and not changes:
        return {"preset": name}
    base = PRESETS[name] if name and name != "custom" else resolve(current)
    return normalize({"preset": "custom", "parts": {**base, **changes}})


# Where a Model API Resident keeps Portal's system parts; each agent Provider
# declares its own (``AgentProvider.system_instructions_place``).
_MODEL_API_SYSTEM_PLACE = "到期时作为第一条 system 消息"


def resident_md_transport(provider: str) -> str:
    from management_portal.society_service.resident_execution_provider import initial_instructions_transport_for

    return initial_instructions_transport_for(provider)


def placements(provider: str) -> dict[str, str]:
    """Where each category goes for a Provider; Portal decides, not the user."""
    from management_portal.execution.provider_catalog import AGENT_PROVIDERS

    definition = AGENT_PROVIDERS.get(str(provider or ""))
    system = definition.system_instructions_place if definition else _MODEL_API_SYSTEM_PLACE
    return {
        "core_rules": system,
        "resident_md": system if resident_md_transport(provider) == "system_instructions" else "本轮消息前",
        "runtime_identity": "本轮消息前",
        "authorization_context": "本轮消息后",
        "message_envelope": "包住本轮消息",
        "turn_skills": "本轮消息前；连同核心规则重发时随核心规则",
    }


def describe(value: object, *, provider: str = "") -> dict[str, Any]:
    """The setting as a client shows it: preset, categories, their places."""
    clean = normalize(value)
    choices = resolve(clean)
    places = placements(provider)
    return {
        "preset": clean["preset"],
        "preset_label": PRESET_LABELS[clean["preset"]],
        "presets": [{"id": name, "label": label} for name, label in PRESET_LABELS.items()],
        "parts": [
            {
                "id": name,
                "label": label,
                "description": description,
                "choice": choices[name],
                "choices": [{"id": choice, "label": CHOICE_LABELS[choice]} for choice in allowed],
                "placement": places[name],
            }
            for name, (label, description, allowed) in CATEGORIES.items()
        ],
    }

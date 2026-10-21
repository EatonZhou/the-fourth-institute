from __future__ import annotations
from management_common.portal_paths import path as portal_path

from collections.abc import Iterable
import re
from pathlib import Path

from management_common.skill_paths import normalize_additional_skills
from management_portal.agent_skill_context import PORTAL_SYSTEM_SKILL, materialize_portal_skills
from management_portal.execution.contracts import InjectionPart, SystemInstructions


_SKILL_RESOURCE_ID = re.compile(r"^skill_[0-9a-f]{12}$")


def _resolve_skill_resource_path(skill_id: str, *, actor: str) -> str:
    from management_portal.resources import get_resource_service

    resolved = get_resource_service().resolve(skill_id, actor=actor)
    if (
        str(resolved.get("resource_type") or "").strip() != "skill"
        or not resolved.get("exists", True)
    ):
        raise ValueError(f"Skill resource is unavailable: {skill_id}")
    path = str(resolved.get("resolved_path") or "").strip()
    if not path.endswith("/SKILL.md"):
        raise ValueError(f"Skill resource has no SKILL.md entry: {skill_id}")
    return path


def _additional_skill_paths(skills: Iterable[object], *, skill_root: str, actor: str) -> list[str]:
    root = Path(skill_root).resolve(strict=False)
    references = [
        _resolve_skill_resource_path(str(skill).strip(), actor=actor)
        if _SKILL_RESOURCE_ID.fullmatch(str(skill).strip()) else skill
        for skill in list(skills)[:12]
    ]
    normalized = normalize_additional_skills(references, skill_root=skill_root)
    paths = [str(Path(skill) if skill.endswith("/SKILL.md") else (root / skill / "SKILL.md").resolve())
             for skill in normalized]
    entry_path = str(root / PORTAL_SYSTEM_SKILL / "SKILL.md")
    return list(dict.fromkeys(path for path in paths if path != entry_path))


def portal_system_instructions(
    role: str,
    *,
    additional_skills: Iterable[object] = (),
    injection_policy: str = "session_start",
    allow_subagents: bool = False,
    skill_root: str = str(portal_path("share_root") / "Skills"),
    actor: str = "",
) -> SystemInstructions:
    """Build the Portal-owned lifecycle envelope for agent-loaded Skills.

    Portal owns the role and lifecycle wording. Stable core rules are resolved
    and content-addressed server-side; task-specific Skills remain explicit
    references so their normal discovery semantics are preserved.
    """

    requested_role = str(role or "").strip()
    materialized = materialize_portal_skills(
        requested_role,
        skill_root=skill_root,
    )
    clean_role = materialized.role
    clean_injection_policy = str(
        injection_policy or "session_start"
    ).strip()
    additional_paths = _additional_skill_paths(additional_skills, skill_root=skill_root, actor=actor)
    if clean_injection_policy == "every_turn":
        lifecycle_lines = [
            "这是 Portal 为当前 turn 明确发送的规则刷新指令。",
            "请在本轮继续遵循以下由 Portal 服务端物化的核心规则。",
        ]
    elif clean_injection_policy == "session_start_and_after_compaction":
        lifecycle_lines = [
            "这是当前 Provider session 的启动指令，在整个 session 中有效。",
            (
                "Portal 在 session 开始时发送这些核心规则；context compaction 之后按注入计划发送，"
                "若 Provider 保留系统位置的内容，则不重复发送该部分。"
                "无论在系统提示、会话历史还是压缩内容中看到本段，都不要重复读取这些 Skill。"
            ),
        ]
    else:
        lifecycle_lines = [
            "这是当前 Provider session 的启动指令，在整个 session 中有效。",
            (
                "Portal 不在每个 turn 刷新这些核心规则；无论在系统提示、会话历史还是 "
                "context compaction 中看到本段，都不要重复读取这些 Skill。"
            ),
        ]
    lines = [
        f'<portal-system-prompt role="{clean_role}">',
        *lifecycle_lines,
    ]
    lines.append(materialized.content)
    if clean_role == "resident":
        from management_common.async_questions import QUESTION_CONTENT_INSTRUCTIONS

        lines.append(QUESTION_CONTENT_INSTRUCTIONS)
    if additional_paths:
        lines.extend(
            [
                "完成统一入口后，再读取以下任务专用 Skill：",
                *(f"- {path}" for path in additional_paths),
            ]
        )
    if allow_subagents:
        lines.append("允许使用sub agent。")
    lines.extend(
        [
            (
                "本段由 Portal 显式注入；不要把 AGENTS.md 或平台私有指令"
                "当作本段来源。"
            ),
            "</portal-system-prompt>",
        ]
    )
    return SystemInstructions(
        parts=(InjectionPart("core_rules", "\n".join(lines), clean_injection_policy, "system"),),
    ).validate()


def portal_turn_instructions(
    *,
    additional_skills: Iterable[object] = (),
    skill_root: str = str(portal_path("share_root") / "Skills"),
    actor: str = "",
) -> SystemInstructions | None:
    """Build task Skill references for this turn; startup is planned separately."""

    additional_paths = _additional_skill_paths(additional_skills, skill_root=skill_root, actor=actor)
    if not additional_paths:
        return None
    lines = [
        "<portal-turn-prompt>",
        "这是 Portal 为当前 turn 明确发送的任务增量；不包含也不刷新 Provider session 的启动指令。",
    ]
    if additional_paths:
        lines.extend(
            [
                "读取以下任务专用 Skill：",
                *(f"- {path}" for path in additional_paths),
            ]
        )
    lines.append("</portal-turn-prompt>")
    return SystemInstructions(
        parts=(InjectionPart("turn_skills", "\n".join(lines), "every_turn", "prompt_prefix"),),
    ).validate()

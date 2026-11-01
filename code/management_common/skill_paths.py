"""Validate task Skills in the instance's shared root or the code-owned core set."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

import re
from pathlib import Path


CORE_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "core_skills"
CORE_SKILL_NAMES = frozenset({"tfi-agent-rule", "tfi-unified-id", "manage-knowledge-base", "create-skill"})


def normalize_additional_skills(value: object, *, skill_root: str = str(portal_path('skills'))) -> list[str]:
    raw = value if isinstance(value, (list, tuple)) else []
    skills: list[str] = []
    root = Path(skill_root).resolve(strict=False)
    core = CORE_SKILLS_ROOT.resolve()
    for item in raw[:12]:
        name = str(item or "").strip()
        if not name:
            continue
        if name.endswith(("/SKILL.md", "\\SKILL.md")):
            path = Path(name).expanduser().resolve(strict=False)
            normalized = str(path)
        else:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,78}", name):
                raise ValueError(f"无效的 task-specific Skill 名称：{name}")
            path = ((core if name in CORE_SKILL_NAMES else root) / name / "SKILL.md").resolve(strict=False)
            normalized = str(path) if name in CORE_SKILL_NAMES else name
        if not path.is_relative_to(root) and not any(path == core / name / "SKILL.md" for name in CORE_SKILL_NAMES):
            raise ValueError(f"task-specific Skill 必须位于 {root} 或代码目录中的核心 Skill")
        if not path.is_file():
            raise FileNotFoundError(f"task-specific Skill 不存在：{path}")
        if normalized not in skills:
            skills.append(normalized)
    return skills

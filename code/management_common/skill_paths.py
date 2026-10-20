"""Shared validation of task Skill references under the configured Skill root."""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

import re
from pathlib import Path


def normalize_additional_skills(value: object, *, skill_root: str = str(portal_path("share_root") / "Skills")) -> list[str]:
    raw = value if isinstance(value, (list, tuple)) else []
    skills: list[str] = []
    root = Path(skill_root).resolve(strict=False)
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
            path = (root / name / "SKILL.md").resolve(strict=False)
            normalized = name
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"task-specific Skill 必须位于 {root}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"task-specific Skill 不存在：{path}")
        if normalized not in skills:
            skills.append(normalized)
    return skills

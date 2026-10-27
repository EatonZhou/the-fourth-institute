"""Resolve explicitly selected Skill references at Portal's prompt boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from management_common.skill_paths import normalize_additional_skills
from management_common.portal_paths import path as portal_path


def _identity(ref: str) -> str:
    if ref.startswith("skill_"):
        return ref
    path = Path(ref).expanduser() if ref.endswith("/SKILL.md") else portal_path("share_root") / "Skills" / ref / "SKILL.md"
    return str(path.resolve(strict=False))


def selected_skills(settings: dict, supplied: object, *, resident_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    config = settings.get("capability_config") or {}
    selections = config.get("skills", [])
    disabled = {_identity(item["ref"]) for item in selections if not item.get("enabled", True)}
    supplied = supplied if isinstance(supplied, (list, tuple)) else []
    skills = normalize_additional_skills([ref for ref in supplied if _identity(str(ref or "")) not in disabled])
    observations = []
    for item in selections:
        ref = item["ref"]
        if not item.get("enabled", True):
            continue
        try:
            resolved = ref
            if ref.startswith("skill_"):
                from management_portal.execution.harness_isa_tool import run_instruction
                receipt = run_instruction(resident_id, {"instruction": "ResolveResource", "arguments": {"address": ref}})
                if receipt.get("status") != "committed":
                    raise ValueError("Skill resource is unavailable")
                resolved = (receipt.get("result", {}).get("target") or {}).get("resolved_path", "")
                if not resolved or Path(resolved).name != "SKILL.md":
                    raise ValueError("Skill reference must resolve to SKILL.md")
            normalized = normalize_additional_skills([resolved])
            if normalized and normalized[0] not in skills:
                if len(skills) >= 12:
                    raise ValueError("At most 12 selected Skills can be attached to a turn")
                skills.extend(normalized)
            observations.append({"ref": ref, "observation": "found", "material": "reference", "body_sent": False})
        except (ValueError, FileNotFoundError) as exc:
            if item.get("required"):
                raise ValueError(f"Required Skill unavailable: {ref}: {exc}") from exc
            observations.append({"ref": ref, "observation": "unknown", "reason": type(exc).__name__})
    return skills, observations

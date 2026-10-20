from __future__ import annotations
from management_common.portal_paths import path as portal_path

import os
import copy
import time
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .resource_refs import append_resource_references, resolve_resource_references


HOST_SKILLS_ROOT = Path(
    os.getenv("PORTAL_AGENT_CONTEXT_SKILLS_ROOT", str(portal_path("share_root") / "Skills"))
).expanduser()
HOST_AGENTS_ROOT = Path(
    os.getenv("PORTAL_AGENT_CONTEXT_AGENTS_ROOT", str(portal_path("share_root") / "Agents"))
).expanduser()
THIRD_PARTY_SKILLS_ROOT = HOST_SKILLS_ROOT / "third-party"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _markdown_identity(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    title = ""
    description = ""
    lines = text.splitlines()
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index, raw in enumerate(lines[1:200], start=1):
            if raw.strip() == "---":
                body_start = index + 1
                break
            key, separator, value = raw.partition(":")
            if not separator:
                continue
            clean_value = value.strip().strip("\"'")
            if key.strip() in {"name", "title"} and not title:
                title = clean_value
            elif key.strip() in {"description", "summary"} and not description:
                description = clean_value
    for raw in lines[body_start:body_start + 80]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not title:
            title = line.lstrip("#").strip()
            continue
        if not line.startswith(("#", "---")) and not description:
            description = line[:180]
            break
    return title, description


def _list_context_directory(
    host_root: Path,
    marker_name: str,
    *,
    id_prefix: str = "",
    excluded_roots: set[Path] | None = None,
) -> list[dict[str, str]]:
    if not host_root.is_dir():
        return []
    excluded = {
        root.resolve(strict=False)
        for root in excluded_roots or set()
    }
    items: list[dict[str, str]] = []
    for child in sorted(host_root.iterdir(), key=lambda item: item.name.casefold()):
        if (
            not child.is_dir()
            or child.name.startswith(".")
            or child.resolve(strict=False) in excluded
        ):
            continue
        marker = child / marker_name
        if not marker.is_file():
            continue
        item_id = f"{id_prefix}{child.name}"
        title, description = _markdown_identity(marker)
        items.append({
            "id": item_id,
            "name": title or item_id,
            "path": str(child),
            "description": description,
        })
    return items


def list_options() -> dict[str, list[dict[str, str]]]:
    return copy.deepcopy(_cached_options(
        HOST_SKILLS_ROOT, HOST_AGENTS_ROOT, THIRD_PARTY_SKILLS_ROOT, int(time.monotonic() // 60),
    ))


@lru_cache(maxsize=8)
def _cached_options(skills_root: Path, agents_root: Path, third_party_root: Path, refresh_bucket: int) -> dict[str, list[dict[str, str]]]:
    skills = _list_context_directory(
        skills_root,
        "SKILL.md",
        excluded_roots={third_party_root},
    )
    skills.extend(_list_context_directory(
        third_party_root,
        "SKILL.md",
        id_prefix="third-party/",
    ))
    return {
        "skills": skills,
        "agents": _list_context_directory(
            agents_root,
            "README.md",
            excluded_roots={Path(folder) for folder, _lineage, _owner in _personal_folders()} if agents_root.is_dir() else set(),
        ),
    }


def _personal_folders() -> list[tuple[str, str, str]]:
    """Personal AMA Lineage folders: private to their owners, never shared agent context."""
    from .society_service.lineage_access_policy import personal_folders

    return personal_folders()


def _selection_ids(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    selected: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            value = str(item.get("id") or item.get("path") or "").strip()
        else:
            value = str(item or "").strip()
        if "/" in value and not value.startswith("third-party/"):
            normalized = value.replace("\\", "/")
            candidates = [
                normalized[normalized.index(marker) + len(marker):].strip("/")
                for marker in ("/Skills/", "/Agents/")
                if marker in normalized
            ]
            candidates.append(PurePosixPath(normalized).name)
            value = candidates[0]
        if value and value not in seen:
            selected.append(value)
            seen.add(value)
    return selected


def _selected_option(kind: str, item_id: str, private: list) -> dict[str, str] | None:
    """Resolve one explicit selection without discovering unrelated options."""
    from .society_service.lineage_access_policy import personal_area

    parts = PurePosixPath(item_id).parts
    if (not parts or any(part.startswith(".") for part in parts)
            or (len(parts) != 1 and not (kind == "skills" and len(parts) == 2 and parts[0] == "third-party"))
            or item_id == "third-party"):
        return None
    root = HOST_SKILLS_ROOT if kind == "skills" else HOST_AGENTS_ROOT
    child = root.joinpath(*parts)
    if not child.is_dir() or not child.resolve().is_relative_to(root.resolve()):
        return None
    if kind == "agents" and personal_area(str(child), private):
        return None
    marker = child / ("SKILL.md" if kind == "skills" else "README.md")
    if not marker.is_file() or not marker.resolve().is_relative_to(root.resolve()):
        return None
    title, description = _markdown_identity(marker)
    return {"id": item_id, "name": title or item_id, "path": str(child), "description": description}


def resolve(
    selection: object,
    *,
    actor: str = "",
    content: object = "",
) -> dict[str, Any]:
    if selection in (None, ""):
        selection = {}
    if not isinstance(selection, dict):
        raise ValueError("agent context 必须是 object")

    resolved: dict[str, Any] = {
        "skills": [],
        "agents": [],
        "updated_at": _now_iso(),
        "updated_by": str(actor or ""),
    }
    invalid: list[str] = []
    for key in ("skills", "agents"):
        selected = _selection_ids(selection.get(key))
        private = _personal_folders() if selected and key == "agents" else []
        for item_id in selected:
            item = _selected_option(key, item_id, private)
            if item is None:
                invalid.append(f"{key}:{item_id}")
            else:
                resolved[key].append(dict(item))
    if invalid:
        raise ValueError("无效的 agent context 选项: " + ", ".join(invalid))

    resources = resolve_resource_references(
        selection.get("resources"),
        content=content,
        actor=actor,
    )
    if resources:
        resolved["resources"] = resources
    return resolved


def marker_path(kind: str, item_id: str) -> Path:
    relative = PurePosixPath(str(item_id or "").strip())
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("invalid agent context item ID")
    if kind == "skills":
        return HOST_SKILLS_ROOT.joinpath(*relative.parts, "SKILL.md")
    if kind == "agents":
        return HOST_AGENTS_ROOT.joinpath(*relative.parts, "README.md")
    raise ValueError(f"unsupported agent context kind: {kind}")


def prompt_references(
    context_refs: object,
    *,
    item_char_limit: int,
    total_char_limit: int,
) -> str:
    """Render selected Agent context through one capability-neutral contract."""

    from .society_service.lineage_access_policy import personal_area

    refs = context_refs if isinstance(context_refs, dict) else {}
    blocks: list[str] = []
    remaining = max(0, int(total_char_limit))
    per_item_limit = max(0, int(item_char_limit))
    private = None
    for key, label in (("skills", "Skill"), ("agents", "Agent")):
        for item in refs.get(key) or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id or remaining <= 0:
                continue
            marker = marker_path(key, item_id)
            if key == "agents":
                # A selection saved before AMA folders left the options.
                private = _personal_folders() if private is None else private
                if personal_area(str(marker), private):
                    continue
            try:
                content = marker.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).strip()
            except (OSError, ValueError):
                content = ""
            item_limit = min(per_item_limit, remaining)
            truncated = len(content) > item_limit
            if truncated:
                content = content[:item_limit]
            remaining -= len(content)
            blocks.append(
                f'<reference type="{label}" id="{item_id}">\n'
                f'name: {str(item.get("name") or item_id)}\n'
                f'display_path: {str(item.get("path") or "")}\n'
                f'content_truncated: {"yes" if truncated else "no"}\n'
                "content:\n"
                f"{content or '(empty or unreadable)'}\n"
                "</reference>"
            )
    return append_resource_references("\n\n".join(blocks), refs)

"""Project Research Maps and their resource-backed task summaries."""

from pathlib import Path
import re
from typing import Any

from knowledge_board_lan.knowledge_backend import _parse_front_matter
from management_portal.resources.providers.project import PROJECTS_ROOT


def _plain_markdown_excerpt(text: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"^\*\*([^*]+)\*\*:\s*", r"\1: ", value)
    return value[:limit].rstrip()


def _markdown_section(body: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$"
    match = re.search(pattern, body, flags=re.M)
    if not match:
        return ""
    next_match = re.search(r"^##\s+", body[match.end():], flags=re.M)
    end = match.end() + next_match.start() if next_match else len(body)
    return body[match.end():end].strip()


def _parse_research_tasks_block(block: str) -> list[dict]:
    tasks: list[dict] = []
    for raw in str(block or "").splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        item = line[1:].strip()
        if not item or item.lower().startswith("none"):
            continue
        match = re.match(r"^(todo_[A-Za-z0-9]+)\s*(?::\s*(\w+))?", item)
        if not match:
            continue
        tasks.append({
            "todo_id": match.group(1),
            "map_status": (match.group(2) or "").strip(),
        })
    return tasks


def _parse_research_map_body(body: str) -> dict | None:
    title_match = re.search(r"\*\*Research Map:\s*([^*]+?)\*\*:\s*(.+?)(?:\n\n|$)", body, flags=re.S)
    if not title_match:
        return None
    topic_title = _plain_markdown_excerpt(title_match.group(1), 80)
    summary = _plain_markdown_excerpt(title_match.group(2), 240)
    topic = _plain_markdown_excerpt(_markdown_section(body, "Topic"), 320)
    background = _plain_markdown_excerpt(_markdown_section(body, "背景和边界"), 420)
    directions_text = _markdown_section(body, "Directions")
    directions: list[dict] = []
    matches = list(re.finditer(r"^###\s+(D\d+)\s*:\s*(.+?)\s*$", directions_text, flags=re.M))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(directions_text)
        chunk = directions_text[start:end].strip()
        status_match = re.search(r"^状态\s*[：:]\s*(.+?)\s*$", chunk, flags=re.M)
        desc_match = re.search(r"^说明\s*[：:]\s*(.+?)(?:\n\nResearch Tasks\s*[：:]|$)", chunk, flags=re.M | re.S)
        tasks_match = re.search(r"^Research Tasks\s*[：:]\s*(.+)$", chunk, flags=re.M | re.S)
        directions.append({
            "key": match.group(1),
            "title": _plain_markdown_excerpt(match.group(2), 120),
            "status": _plain_markdown_excerpt(status_match.group(1), 40) if status_match else "",
            "description": _plain_markdown_excerpt(desc_match.group(1), 320) if desc_match else "",
            "tasks": _parse_research_tasks_block(tasks_match.group(1) if tasks_match else ""),
        })
    return {
        "topic_title": topic_title,
        "summary": summary,
        "topic": topic,
        "background": background,
        "directions": directions,
    }


def list_research_maps(project_id: str, *, actor: str, service: Any,
                       projects_base: Path | None = None) -> list[dict]:
    """Read one authorized project's plans; resource records own task state."""
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ValueError("A Project ID is required.")
    project = service.get(project_id, actor=actor)
    if project.get("resource_type") != "project":
        raise ValueError("Expected a Project resource.")
    root = (projects_base or PROJECTS_ROOT).resolve()
    plan_dir = (root / project_id / "knowledge_base" / "plan").resolve()
    if not plan_dir.is_relative_to(root):
        raise PermissionError("Project plans are outside the projects root.")
    maps = []
    tasks = {}
    for path in sorted(plan_dir.glob("*.md")):
        if not path.resolve().is_relative_to(plan_dir):
            raise PermissionError("Research Map is outside the plan directory.")
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        parsed = _parse_research_map_body(body)
        if parsed is None:
            continue
        map_id = str(meta.get("kb_id") or "").strip()
        if not re.fullmatch(r"plan_[0-9a-f]{12}", map_id):
            raise ValueError(f"Research Map {path.name} requires a valid kb_id.")
        try:
            service.get(map_id, actor=actor)
        except (KeyError, FileNotFoundError, PermissionError):
            continue
        for direction in parsed["directions"]:
            for task in direction["tasks"]:
                todo_id = task["todo_id"]
                if todo_id not in tasks:
                    try:
                        tasks[todo_id] = service.get(todo_id, actor=actor)
                    except (KeyError, FileNotFoundError, PermissionError):
                        tasks[todo_id] = {}
                record = tasks[todo_id]
                task["title"] = record.get("title") or todo_id
                task["status"] = (record.get("attributes") or {}).get("status") or task["map_status"]
        maps.append({
            "id": map_id,
            "project_id": project_id,
            "created_at": str(meta.get("created_at") or ""),
            "updated_at": path.stat().st_mtime,
            **parsed,
        })
    maps.sort(key=lambda item: (-item["updated_at"], item["id"]))
    return maps

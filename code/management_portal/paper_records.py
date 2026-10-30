"""Paper record fields: timestamps, slugs, lists, venues, authors, identifiers and Project relations."""

from __future__ import annotations

from datetime import datetime
import re
import secrets
import time
from urllib.parse import urlparse


NON_PROJECT_RELATION_IDS = {"general", "references", "auto collected", "auto-collected", "unsorted"}


def _is_project_relation_id(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.casefold() not in NON_PROJECT_RELATION_IDS


def _is_non_project_tag(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    key = text.casefold()
    if key.startswith("project:"):
        key = key.split(":", 1)[1].strip()
    return key in NON_PROJECT_RELATION_IDS


def _project_id_from_tag(value: object) -> str:
    text = str(value or "").strip()
    if text.casefold().startswith("project:"):
        return text.split(":", 1)[1].strip()
    return text


def _is_project_tag(value: object, project_ids: set[str]) -> bool:
    return _project_id_from_tag(value) in project_ids


def _now() -> float:
    return round(time.time(), 6)


def _coerce_timestamp(value, default: int | None = None) -> int:
    if default is None:
        default = _now()
    try:
        return int(value)
    except Exception:
        pass
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return default


def _normalize_fingerprint_text(value: str) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _paper_authors_string(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())[:600]
    return str(value or "").strip()[:600]


def _paper_authors_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[,;，、]\s*", str(value or "")) if item.strip()]


def _normalize_list(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,，\n]+", str(value or ""))
    result = []
    seen = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text[:80])
    return result


def _project_ids_from_paper(paper: dict) -> list[str]:
    values: list[str] = []
    values.extend(str(item) for item in paper.get("project_ids") or [] if _is_project_relation_id(item))
    relations = paper.get("relations") if isinstance(paper.get("relations"), dict) else {}
    for item in relations.get("projects") or []:
        if isinstance(item, dict):
            project_id = str(item.get("project_id") or "").strip()
        else:
            project_id = str(item or "").strip()
        if _is_project_relation_id(project_id):
            values.append(project_id)
    session = str(paper.get("session") or "").strip()
    if _is_project_relation_id(session):
        values.append(session)
    return _normalize_list(values)


def _merge_project_tags(tags: list[str], paper: dict) -> list[str]:
    project_ids_list = _project_ids_from_paper(paper)
    project_ids = set(project_ids_list)
    values = [
        str(tag)
        for tag in tags or []
        if not _is_non_project_tag(tag) and not _is_project_tag(tag, project_ids)
    ]
    return _normalize_list(values)


def _paper_project_filter_values(paper: dict) -> list[str]:
    values: list[str] = []
    values.extend(str(item) for item in paper.get("project_ids") or [] if _is_project_relation_id(item))
    relations = paper.get("relations") if isinstance(paper.get("relations"), dict) else {}
    for item in relations.get("projects") or []:
        if isinstance(item, dict):
            if _is_project_relation_id(item.get("project_id")):
                values.append(str(item.get("project_id") or ""))
        else:
            if _is_project_relation_id(item):
                values.append(str(item or ""))
    for tag in paper.get("tags") or []:
        text = str(tag or "").strip()
        if text.casefold().startswith("project:") and not _is_non_project_tag(text):
            values.append(text.split(":", 1)[1].strip())
    return _normalize_list(values)


def _registered_project_ids() -> set[str]:
    from management_portal import project_registry

    return {str(item.get("id") or "").strip() for item in project_registry.projects()}


def _merge_paper_relations(existing: dict, paper: dict) -> dict:
    """Merge project relations, keeping only Projects that are registered.

    Collection jobs passed journals and topics such as "Science Robotics" as the
    paper's project, which left relations to Projects that never existed.
    """
    relations = dict(existing or {})
    existing_projects = relations.get("projects") if isinstance(relations.get("projects"), list) else []
    by_project: dict[str, dict] = {}
    for item in existing_projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or "").strip()
        if _is_project_relation_id(project_id):
            by_project[project_id] = dict(item)
    incoming = paper.get("relations") if isinstance(paper.get("relations"), dict) else {}
    incoming_projects = incoming.get("projects") if isinstance(incoming.get("projects"), list) else []
    for item in incoming_projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or "").strip()
        if _is_project_relation_id(project_id):
            by_project[project_id] = {**by_project.get(project_id, {}), **dict(item), "project_id": project_id}
    for project_id in _project_ids_from_paper(paper):
        by_project.setdefault(
            project_id,
            {
                "project_id": project_id,
                "relation": "relevant",
                "source": str(paper.get("created_by") or ""),
            },
        )
    project_ids = set(by_project)
    registered = _registered_project_ids()
    relations["projects"] = sorted(
        (item for project_id, item in by_project.items() if project_id in registered),
        key=lambda item: str(item.get("project_id") or ""),
    )
    topics = relations.get("topics") if isinstance(relations.get("topics"), list) else []
    relations["topics"] = _normalize_list(
        [
            tag for tag in [*topics, *(paper.get("tags") or [])]
            if not _is_non_project_tag(tag) and not _is_project_tag(tag, project_ids)
        ]
    )
    return relations


def _slugify(text: str) -> str:
    raw = str(text or "").strip().lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw[:80] or secrets.token_hex(4)


def _is_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_year(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(19|20)\d{2}", text)
    return match.group(0) if match else text[:16]


def _normalize_venue(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return "Unsorted"
    if text.casefold().startswith("arxiv"):
        return "arXiv"
    return text[:80] or "Unsorted"


def _arxiv_id_from_text(text: str) -> str:
    patterns = [
        r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?",
        r"\barXiv\s*:\s*([0-9]{4}\.[0-9]{4,5})(?:v\d+)?",
        r"\b([0-9]{4}\.[0-9]{4,5})(?:v\d+)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            return match.group(1)
    return ""

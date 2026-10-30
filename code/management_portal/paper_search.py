"""Papers from the resource index and the library query filter."""

from __future__ import annotations

from datetime import datetime

from management_portal.paper_records import _coerce_timestamp, _paper_project_filter_values
from management_portal.paper_store import _paper_public, _read_text_preview, _safe_file_path, _summary_text
from management_portal.resources import get_resource_service


def _paper_search_text(paper: dict) -> str:
    parts = []
    for key in ("id", "paper_id", "title", "authors", "venue", "year", "session", "folder", "abstract", "status"):
        parts.append(str(paper.get(key) or ""))
    for key in ("tags", "workload", "platform", "architecture", "optimization"):
        parts.extend(str(x) for x in paper.get(key) or [])
    if paper.get("_store_root") or paper.get("analysis_path"):
        analysis_path = _safe_file_path(str(paper.get("analysis_path") or ""))
        parts.append(_read_text_preview(analysis_path, limit=40_000))
        parts.append(_summary_text(paper, limit=4000))
    return "\n".join(parts).casefold()


def _filter_papers(papers: list[dict], params: dict) -> list[dict]:
    query = str(params.get("q") or "").strip().casefold()
    year = str(params.get("year") or "").strip()
    venue = str(params.get("venue") or "").strip().casefold()
    tag = str(params.get("tag") or "").strip().casefold()
    project = str(params.get("project") or "").strip().casefold()
    analysis = str(params.get("analysis") or "").strip().lower()
    registered = str(params.get("registered") or "").strip().lower()
    today = datetime.now().date()
    result = []
    for paper in papers:
        public = paper if {"has_pdf", "has_analysis", "has_summary"}.issubset(paper.keys()) else _paper_public(paper)
        if query and query not in _paper_search_text(paper):
            continue
        if year and str(paper.get("year") or "") != year:
            continue
        if venue and str(paper.get("venue") or "").casefold() != venue:
            continue
        if analysis == "ready" and not public["has_analysis"]:
            continue
        if analysis == "missing" and public["has_analysis"]:
            continue
        if registered == "today":
            created_at = _coerce_timestamp(paper.get("created_at"), default=0)
            if not created_at or datetime.fromtimestamp(created_at).date() != today:
                continue
        if tag:
            all_tags = []
            for key in ("tags", "workload", "platform", "architecture", "optimization"):
                all_tags.extend(str(x).casefold() for x in paper.get(key) or [])
            if tag not in all_tags:
                continue
        if project:
            project_values = []
            for value in _paper_project_filter_values(paper):
                text = str(value or "").strip().casefold()
                if text:
                    project_values.append(text)
                    project_values.append(f"project:{text}")
            if project not in project_values:
                continue
        result.append(public)
    return result


def resource_index_papers(*, actor: str = "") -> list[dict]:
    from management_portal import resource_views

    return resource_views.paper_records(
        actor=actor,
        service=get_resource_service(),
    )


def semantic_search_catalog(*, actor: str, project: str = "") -> list[dict]:
    papers = resource_index_papers(actor=actor)
    return _filter_papers(papers, {"project": project}) if project else papers

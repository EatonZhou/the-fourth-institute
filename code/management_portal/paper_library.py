"""The library page: the paper list with its folders, filters and workflow, and the API description."""

from __future__ import annotations

from management_portal import paper_favorites, semantic_search_jobs
from management_portal.paper_collection_jobs import (
    _paper_collection_filter_summary, _paper_collection_result_map,
)
from management_portal.paper_records import _paper_project_filter_values
from management_portal.paper_reviews import REVIEW_LENSES, _all_reviews
from management_portal.paper_search import _filter_papers, resource_index_papers
from management_portal.paper_store import DATA_ROOT, LOCK, PAPER_STORE_ROOT


WORKFLOW_STEPS = [
    {
        "id": "search",
        "title": "Paper deep search",
        "summary": "围绕 topic、venue 或已有 seed paper 收集候选论文，形成可复用的 paper manifest。",
    },
    {
        "id": "read",
        "title": "Paper deep read",
        "summary": "读取 PDF、figure/table 和 captions，产出结构化 analysis markdown。",
    },
    {
        "id": "library",
        "title": "Paper library",
        "summary": "把 PDF、analysis、metadata 和 tags 放进同一套检索入口。",
    },
    {
        "id": "assistant",
        "title": "Research chat",
        "summary": "用库内 metadata、analysis 和 PDF text 支持单篇或全库问答。",
    },
    {
        "id": "review",
        "title": "Multi-reviewer review",
        "summary": "按 novelty、method、evaluation 三个视角生成独立评审和 author action memo。",
    },
]


def _paper_library_list_item(paper: dict) -> dict:
    keys = (
        "id", "title", "authors", "venue", "year", "session",
        "tags", "workload", "platform", "architecture", "optimization",
        "created_at", "updated_at", "has_pdf", "has_analysis", "has_summary",
        "semantic_rank", "semantic_score", "semantic_reason", "collection_rank",
    )
    item = {key: paper.get(key) for key in keys if paper.get(key) not in (None, "", [])}
    abstract = str(paper.get("abstract") or "").strip()
    if abstract:
        item["abstract"] = abstract[:300]
    return item


def _paper_library_tree_item(paper: dict) -> dict:
    """Fields needed to browse and export papers before their row is shown."""
    return {
        key: paper.get(key)
        for key in ("id", "title", "venue", "year", "session", "has_pdf")
        if paper.get(key) not in (None, "")
    }


def _paper_library_page_number(value: object, default: int, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _paper_query_sort_key(paper: dict, query: object) -> tuple[int, int, str, str]:
    needle = str(query or "").strip().casefold()
    paper_id = str(paper.get("id") or "").strip().casefold()
    title = str(paper.get("title") or "").strip().casefold()
    if needle and (paper_id == needle or title == needle):
        relevance = 0
    elif needle and (paper_id.startswith(needle) or title.startswith(needle)):
        relevance = 1
    elif needle and (needle in paper_id or needle in title):
        relevance = 2
    else:
        tags = []
        for key in ("tags", "workload", "platform", "architecture", "optimization"):
            tags.extend(str(value or "").strip().casefold() for value in paper.get(key) or [])
        if needle and any(value == needle for value in tags):
            relevance = 3
        elif needle and any(needle in value for value in tags):
            relevance = 4
        else:
            metadata = "\n".join(
                str(paper.get(key) or "")
                for key in ("authors", "venue", "year", "session", "folder", "abstract", "status")
            ).casefold()
            relevance = 5 if needle and needle in metadata else 6
    try:
        year = -int(str(paper.get("year") or "0").strip())
    except ValueError:
        year = 0
    return relevance, year, str(paper.get("venue") or "").casefold(), title


def _folder_tree(papers: list[dict], *, include_papers: bool = True) -> list[dict]:
    by_venue: dict[str, dict] = {}
    for paper in papers:
        venue = str(paper.get("venue") or "Unsorted")
        year = str(paper.get("year") or "Unknown")
        session = str(paper.get("session") or "General")
        venue_node = by_venue.setdefault(venue, {"name": venue, "count": 0, "children": {}})
        venue_node["count"] += 1
        year_node = venue_node["children"].setdefault(year, {"name": year, "count": 0, "children": {}})
        year_node["count"] += 1
        session_node = year_node["children"].setdefault(session, {"name": session, "count": 0, "papers": []})
        session_node["count"] += 1
        if include_papers:
            session_node["papers"].append({
                "id": paper["id"],
                "title": paper["title"],
                "has_analysis": paper.get("has_analysis", False),
                "has_pdf": paper.get("has_pdf", False),
            })
    venues = []
    for venue in sorted(by_venue.values(), key=lambda item: (-item["count"], item["name"])):
        years = []
        for year in sorted(venue["children"].values(), key=lambda item: str(item["name"]), reverse=True):
            sessions = sorted(year["children"].values(), key=lambda item: (-item["count"], item["name"]))
            year["children"] = sessions
            years.append(year)
        venue["children"] = years
        venues.append(venue)
    return venues


def _filters(papers: list[dict]) -> dict:
    values = {"years": set(), "venues": set(), "tags": set(), "projects": set(), "workload": set(), "platform": set(), "architecture": set(), "optimization": set()}
    for paper in papers:
        if paper.get("year"):
            values["years"].add(str(paper["year"]))
        if paper.get("venue"):
            values["venues"].add(str(paper["venue"]))
        values["projects"].update(str(x) for x in _paper_project_filter_values(paper) if str(x).strip())
        for key in ("tags", "workload", "platform", "architecture", "optimization"):
            values[key].update(str(x) for x in paper.get(key) or [] if str(x).strip())
    return {
        "years": sorted(values["years"], reverse=True),
        "venues": sorted(values["venues"]),
        "tags": sorted(values["tags"]),
        "projects": sorted(values["projects"]),
        "workload": sorted(values["workload"]),
        "platform": sorted(values["platform"]),
        "architecture": sorted(values["architecture"]),
        "optimization": sorted(values["optimization"]),
    }


def list_library(params: dict | None = None, *, actor: str = "") -> dict:
    params = params or {}
    compact = str(params.get("compact") or "").strip().lower() in {"1", "true", "yes", "on"}
    rows_only = compact and str(params.get("rows_only") or "").strip() == "1"
    semantic_id = str(params.get("semantic") or "").strip()
    collector_id = str(params.get("collector") or "").strip()
    semantic_view = semantic_search_jobs.search_view(semantic_id, actor=actor, surfaces=("paper",)) if semantic_id else None
    papers = resource_index_papers(actor=actor)
    favorites = paper_favorites.favorite_ids(actor)
    with LOCK:
        visible = _filter_papers(papers, params or {})
        reviews = [] if rows_only else _all_reviews()
    if str(params.get("favorite") or "").strip().lower() in {"1", "true", "yes", "on"}:
        selected = set(favorites)
        visible = [paper for paper in visible if paper.get("id") in selected]
    semantic_summary = semantic_view["summary"] if semantic_view else None
    semantic_results = semantic_view["result_map"] if semantic_view else {}
    semantic_rank = {paper_id: int(item.get("rank") or 0) - 1 for paper_id, item in semantic_results.items()}
    collection_summary = None
    collection_results = {}
    collection_rank = {}
    if semantic_id and semantic_summary:
        visible = [
            {
                **paper,
                "semantic_rank": semantic_results[str(paper.get("id") or "")]["rank"],
                "semantic_score": semantic_results[str(paper.get("id") or "")]["score"],
                "semantic_reason": semantic_results[str(paper.get("id") or "")]["reason"],
            }
            for paper in visible
            if str(paper.get("id") or "") in semantic_results
        ]
    elif collector_id:
        collection_summary = _paper_collection_filter_summary(collector_id)
        collection_results = _paper_collection_result_map(collector_id) if collection_summary else {}
        collection_rank = {paper_id: int(item.get("rank") or 0) - 1 for paper_id, item in collection_results.items()}
        if collection_summary:
            visible = [
                {
                    **paper,
                    "collection_rank": collection_results[str(paper.get("id") or "")]["rank"],
                }
                for paper in visible
                if str(paper.get("id") or "") in collection_results
            ]
    analysis_count = sum(1 for paper in papers if paper.get("has_analysis"))
    review_count = len(reviews)
    if semantic_rank:
        sorted_visible = sorted(
            visible,
            key=lambda p: (semantic_rank.get(str(p.get("id") or ""), 10_000), str(p.get("title") or "")),
        )
    elif collection_rank:
        sorted_visible = sorted(
            visible,
            key=lambda p: (collection_rank.get(str(p.get("id") or ""), 10_000), str(p.get("title") or "")),
        )
    elif str(params.get("q") or "").strip():
        sorted_visible = sorted(
            visible,
            key=lambda paper: _paper_query_sort_key(paper, params.get("q")),
        )
    else:
        sorted_visible = sorted(visible, key=lambda p: (str(p.get("year") or ""), str(p.get("venue") or ""), str(p.get("title") or "")), reverse=True)
    if rows_only:
        offset = _paper_library_page_number(params.get("offset"), 0, len(sorted_visible))
        limit = max(1, _paper_library_page_number(params.get("limit"), 100, 500))
        return {
            "papers": [_paper_library_list_item(paper) for paper in sorted_visible[offset:offset + limit]],
        }
    preview_limit = _paper_library_page_number(params.get("preview_limit"), 0, 500) if compact else 0
    if compact and preview_limit:
        library_papers = [
            _paper_library_list_item(paper) if index < preview_limit else _paper_library_tree_item(paper)
            for index, paper in enumerate(sorted_visible)
        ]
    elif compact:
        library_papers = [_paper_library_list_item(paper) for paper in sorted_visible]
    else:
        library_papers = sorted_visible
    return {
        "summary": {
            "paper_count": len(papers),
            "visible_count": len(visible),
            "analysis_count": analysis_count,
            "review_count": review_count,
            "data_root": str(DATA_ROOT),
            "paper_store_root": str(PAPER_STORE_ROOT),
            "semantic_search": semantic_summary,
            "paper_collection": collection_summary,
        },
        "papers": library_papers,
        "favorite_ids": favorites,
        "folders": _folder_tree(visible, include_papers=not compact),
        "filters": _filters(papers),
        "workflow": WORKFLOW_STEPS,
        "review_lenses": REVIEW_LENSES,
    }


def api_docs() -> dict:
    return {
        "endpoints": [
            {"method": "GET", "path": "/paper/api/library", "purpose": "List visible papers, filters, folder tree, workflow metadata, and the current actor's favorite_ids. favorite=1 intersects favorites with other filters."},
            {"method": "POST", "path": "/paper/api/favorites", "purpose": "Set the authenticated actor's personal Paper favorite with {paper_id, favorite: boolean}. Adding requires read access; removal remains possible after access loss."},
            {"method": "GET", "path": "/paper/api/papers/detail?id=...", "purpose": "Load one paper, analysis markdown, PDF URL, and reviews."},
            {"method": "POST", "path": "/paper/api/papers/registration-draft", "purpose": "Upload a PDF and start extracting editable registration metadata."},
            {"method": "GET", "path": "/paper/api/papers/registration-draft?upload_id=...", "purpose": "Read PDF registration metadata extraction status."},
            {"method": "POST", "path": "/paper/api/papers/register", "purpose": "Register metadata plus a PDF registration upload, PDF/analysis paths, or URLs."},
            {"method": "POST", "path": "/paper/api/papers/delete-pdf", "purpose": "Delete the current local PDF file and derived PDF text cache without deleting the paper record."},
            {"method": "POST", "path": "/paper/api/reviews/create", "purpose": "Create a three-lens review draft for a paper."},
            {"method": "POST", "path": "/paper/api/reviews/start", "purpose": "Start or reuse the current actor's background review without waiting for the model."},
            {"method": "GET", "path": "/paper/api/reviews/status?paper_id=...", "purpose": "Recover the current actor's latest review job after reload; render results from paper reviews."},
            {"method": "POST", "path": "/paper/api/deep-read/start", "purpose": "Start a background PDF deep-read job and write analysis markdown."},
            {"method": "GET", "path": "/paper/api/deep-read/status?paper_id=...", "purpose": "Poll deep-read job status for a paper."},
            {"method": "POST", "path": "/paper/api/figures/extract", "purpose": "Extract embedded PDF figures and save figure metadata."},
            {"method": "POST", "path": "/paper/api/figures/explain", "purpose": "Start a background caption/context based explanation job for one figure."},
            {"method": "POST", "path": "/paper/api/figures/generate", "purpose": "Start a background image generation job for one figure (paper:figure-generation)."},
            {"method": "POST", "path": "/paper/api/figures/generated/delete", "purpose": "Delete one generated figure record and its stored image file."},
        ],
        "data_root": str(DATA_ROOT),
        "workflow": WORKFLOW_STEPS,
    }

"""One paper's detail view and its asset files."""

from __future__ import annotations

from pathlib import Path
import re

from management_portal import paper_state_store
from management_portal.paper_figure_jobs import _recover_figure_tasks
from management_portal.paper_figures import _figure_public, _load_figures
from management_portal.paper_records import _is_url
from management_portal.paper_store import (
    LOCK, _latest_paper_run, _paper_bundle_path, _paper_by_id, _paper_public, _paper_root, _paper_store_file,
    _read_text_preview, _refs_for_paper, _safe_file_path, _summary_text,
)


PAPER_DETAIL_SECTIONS = {"analysis", "summary", "exp", "reviews", "notes", "refs", "figures"}


def _paper_detail_sections(sections: object = None) -> set[str]:
    if sections is None or str(sections).strip() == "":
        return set(PAPER_DETAIL_SECTIONS)
    if isinstance(sections, str):
        values = re.split(r"[\s,]+", sections)
    elif isinstance(sections, (list, tuple, set)):
        values = [str(item or "") for item in sections]
    else:
        values = [str(sections)]
    return {value.strip().casefold() for value in values if value.strip().casefold() in PAPER_DETAIL_SECTIONS}


def get_paper_detail(paper_id: str, sections: object = None) -> dict:
    paper_id = str(paper_id or "").strip()
    requested = _paper_detail_sections(sections)
    with LOCK:
        paper = _paper_by_id(paper_id)
        if not paper:
            raise KeyError("paper not found")
        reviews = paper_state_store.records("reviews", paper_id=paper["id"]) if "reviews" in requested else []
        notes = paper_state_store.records("notes", paper_id=paper["id"]) if "notes" in requested else []
        refs = _refs_for_paper(paper) if "refs" in requested else []
        figures = _load_figures(paper) if "figures" in requested else []
    if "figures" in requested:
        _recover_figure_tasks(paper)
        figures = _load_figures(paper)
    public = _paper_public(paper)
    analysis_source = str(paper.get("analysis_path") or "").strip()
    result = {
        "paper": {
            **public,
            "pdf_url": f"/paper/api/asset?id={paper['id']}&kind=pdf" if public["has_pdf"] else "",
            "analysis_url": analysis_source if _is_url(analysis_source) else "",
        },
        "loaded_sections": sorted(requested),
        "assets_job": _latest_paper_run(str(paper.get("id") or ""), kind="assets"),
    }
    if "analysis" in requested:
        analysis_path = _safe_file_path(analysis_source)
        analysis_text = _read_text_preview(analysis_path)
        result["analysis"] = {
            "content": analysis_text,
            "headings": [
                {"level": len(match.group(1)), "title": match.group(2).strip()[:120]}
                for match in re.finditer(r"^(#{1,4})\s+(.+)$", analysis_text, re.MULTILINE)
            ][:40],
            "path": str(analysis_path or analysis_source),
        }
    if "summary" in requested:
        summary_path = _paper_store_file(paper, "summary")
        result["summary"] = {"content": _summary_text(paper), "path": str(summary_path or "")}
    if "exp" in requested:
        exp_path = _paper_store_file(paper, "exp")
        result["exp"] = {
            "content": _read_text_preview(exp_path, limit=120_000).strip() if exp_path and exp_path.is_file() else "",
            "path": str(exp_path or ""),
        }
    if "reviews" in requested:
        result["reviews"] = sorted(reviews, key=lambda item: int(item.get("created_at") or 0), reverse=True)
    if "notes" in requested:
        result["notes"] = sorted(notes, key=lambda item: int(item.get("created_at") or 0), reverse=True)
    if "refs" in requested:
        result["refs"] = sorted(refs, key=lambda item: int(item.get("index") or 0))
    if "figures" in requested:
        result["figures"] = [_figure_public(item, paper["id"]) for item in sorted(figures, key=lambda item: int(item.get("index") or 0))]
    return result


def resolve_asset(paper_id: str, kind: str, asset_id: str = "", generated_id: str = "") -> tuple[Path, str]:
    paper = _paper_for_asset_lookup(paper_id)
    if not paper:
        raise KeyError("paper not found")
    if kind == "pdf":
        path = _paper_store_file(paper, "pdf") or _safe_file_path(str(paper.get("pdf_path") or ""))
        content_type = "application/pdf"
    elif kind == "analysis":
        path = _paper_store_file(paper, "analysis") or _safe_file_path(str(paper.get("analysis_path") or ""))
        content_type = "text/markdown; charset=utf-8"
    elif kind in {"figure", "generated_figure"}:
        source = next((item for item in _load_figures(paper) if str(item.get("figure_id") or "") == str(asset_id or "")), None)
        rel_path = ""
        if source and kind == "figure":
            rel_path = str(source.get("image_rel_path") or "")
        elif source and kind == "generated_figure":
            generated = next((item for item in source.get("generated") or [] if str(item.get("generated_id") or "") == str(generated_id or "")), None)
            rel_path = str((generated or {}).get("image_rel_path") or "")
        paper_root = _paper_root(paper)
        path = _paper_bundle_path(paper_root, rel_path) if paper_root and rel_path else None
        content_type = _media_content_type_for_path(path)
    else:
        raise ValueError("unknown asset kind")
    if not path or not path.is_file():
        raise FileNotFoundError(kind)
    return path, content_type


def _media_content_type_for_path(path: Path | None) -> str:
    suffix = (path.suffix if path else "").casefold()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _paper_for_asset_lookup(paper_id: str) -> dict | None:
    return _paper_by_id(str(paper_id or "").strip())

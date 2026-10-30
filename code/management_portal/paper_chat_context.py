"""Research Chat context of a paper or a collection for the Chat workspace."""

from __future__ import annotations

import json

from management_portal.paper_search import _filter_papers, resource_index_papers
from management_portal.paper_store import (
    LOCK, PAPER_STORE, PAPER_STORE_ROOT, _analysis_text, _paper_public, _paper_root, _paper_store_file,
    _safe_file_path, _summary_text,
)


def _chat_workspace_display(paper: dict | None, collection_label: str) -> dict:
    """What the Research Chat shows for one paper or for a collection."""
    if paper:
        return {
            "title": "Research Chat",
            "subtitle": str(paper.get("title") or "Selected paper"),
            "placeholder": "Ask about this paper...",
            "empty_text": "Ask about this paper.",
            "prompt_context": "",
        }
    return {
        "title": "Research Chat",
        "subtitle": collection_label or "Library Chat",
        "placeholder": "Ask across the paper library...",
        "empty_text": "Compare or summarize the papers in this collection.",
        "prompt_context": "",
    }


def chat_workspace_display_context(*, paper_id: str = "", collection_label: str = "") -> dict:
    paper_id = str(paper_id or "").strip()
    return _chat_workspace_display(PAPER_STORE.paper_by_id(paper_id) if paper_id else None, collection_label)


def chat_workspace_prompt_context(
    *,
    paper_id: str = "",
    query: str = "",
    paper_ids: list[str] | None = None,
    collection_items: list[dict] | None = None,
    collection_label: str = "",
    collection_summary: str = "",
    collection_mode: str = "",
    collection_query: str = "",
    collection_filters: dict | None = None,
) -> dict:
    paper_id = str(paper_id or "").strip()
    query = str(query or "").strip()
    requested_ids = [str(item or "").strip() for item in (paper_ids or []) if str(item or "").strip()]
    requested_items = [item for item in (collection_items or []) if isinstance(item, dict)]
    papers = resource_index_papers()
    with LOCK:
        selected = PAPER_STORE.paper_by_id(paper_id) if paper_id else None
        if selected:
            matches = []
        elif requested_ids:
            by_id = {str(p.get("id") or ""): p for p in papers}
            matches = [by_id[item] for item in requested_ids if item in by_id]
        else:
            matches = _filter_papers(papers, {"q": query})
        if not selected and not matches and not requested_items:
            matches = [_paper_public(p) for p in papers[:10]]
    if selected:
        public = _paper_public(selected)
        analysis_path = _paper_store_file(selected, "analysis") or _safe_file_path(str(selected.get("analysis_path") or ""))
        pdf_path = _safe_file_path(str(selected.get("pdf_path") or ""))
        pdf_text_path = _paper_store_file(selected, "pdf_text_clean")
        prompt_context = f"""Paper Research Chat context:
- selected paper id: {public.get('id') or ''}
- title: {public.get('title') or ''}
- authors: {public.get('authors') or ''}
- venue/year: {public.get('venue') or ''} {public.get('year') or ''}
- paper root: {_paper_root(selected) or ''}
- pdf path: {pdf_path or selected.get('pdf_path') or ''}
- analysis path: {analysis_path or selected.get('analysis_path') or ''}
- pdf text path: {pdf_text_path or ''}

Abstract:
{str(selected.get('abstract') or '')[:3000]}

Summary excerpt:
{_summary_text(selected, limit=2400)}

Analysis excerpt:
{_analysis_text(selected, limit=12000)}
"""
        return {**_chat_workspace_display(public, collection_label), "prompt_context": prompt_context}
    lines = [
        "Paper Research Chat context:",
        "- selected paper: none",
        f"- collection label: {collection_label or 'Paper Library'}",
        f"- collection mode: {collection_mode or 'library'}",
        f"- collection query: {collection_query or ''}",
        f"- collection filters: {json.dumps(collection_filters or {}, ensure_ascii=False)}",
        f"- visible papers in collection: {max(len(matches), len(requested_items))}",
        f"- paper store root: {PAPER_STORE_ROOT}",
        "",
    ]
    if collection_summary:
        lines.extend(["Collection summary:", str(collection_summary)[:3000], ""])
    lines.extend([
        "Candidate papers from current query:",
    ])
    for paper in matches[:30]:
        public = _paper_public(paper)
        lines.append(
            f"- {public.get('id') or ''}: {public.get('title') or ''} "
            f"({public.get('venue') or ''} {public.get('year') or ''})"
        )
        summary = _summary_text(paper, limit=500) or str(public.get("abstract") or "")[:500]
        if summary:
            lines.append(f"  summary: {summary}")
        root = _paper_root(paper)
        if root:
            lines.append(f"  root: {root}")
    if requested_items:
        lines.extend(["", "Collection entries from the current Portal view:"])
        for item in requested_items[:80]:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            authors = str(item.get("authors") or "").strip()
            venue = str(item.get("venue") or "").strip()
            year = str(item.get("year") or "").strip()
            doi = str(item.get("doi") or "").strip()
            url = str(item.get("url") or "").strip()
            registered_id = str(item.get("registered_paper_id") or "").strip()
            lines.append(f"- {title}")
            details = []
            if authors:
                details.append(f"authors: {authors}")
            if venue or year:
                details.append(f"venue/year: {venue} {year}".strip())
            if doi:
                details.append(f"doi: {doi}")
            if url:
                details.append(f"url: {url}")
            if registered_id:
                details.append(f"registered paper id: {registered_id}")
            if details:
                lines.append(f"  {'; '.join(details)}")
    return {**_chat_workspace_display(None, collection_label), "prompt_context": "\n".join(lines)}

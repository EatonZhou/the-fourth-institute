"""A paper's reference list: extracting it from the PDF and registering a reference as a paper."""

from __future__ import annotations

from management_portal.paper_detail import get_paper_detail
from management_portal.paper_records import _now, _slugify
from management_portal.paper_references import (
    _guess_ref_title, _looks_like_bad_ref_title, _looks_like_reference_fragment, _match_registered_paper,
    _parse_references_from_text, _trim_reference_entry,
)
from management_portal.paper_registration_metadata import _lookup_ref_metadata
from management_portal.paper_store import (
    LOCK, PAPER_STORE, _all_papers, _extract_pdf_text_raw, _local_pdf_for_paper, _paper_by_id, _paper_public,
    _paper_store_file, _refs_for_paper, _write_json,
)


def _ref_context(ref_id: str, paper_id: str = "") -> tuple[dict, dict, list[dict]]:
    target_ref_id = str(ref_id or "").strip()
    target_paper_id = str(paper_id or "").strip()
    if not target_ref_id:
        raise ValueError("ref_id required")
    if not target_paper_id:
        raise ValueError("paper_id required")
    paper = _paper_by_id(target_paper_id)
    if not paper:
        raise KeyError("paper not found")
    refs = _refs_for_paper(paper)
    ref = next((item for item in refs if str(item.get("ref_id") or "") == target_ref_id), None)
    if not ref:
        raise KeyError("ref not found")
    return paper, ref, refs


def extract_refs(payload: dict, actor: str) -> dict:
    paper_id = str(payload.get("paper_id") or "").strip()
    if not paper_id:
        raise ValueError("paper_id required")
    with LOCK:
        papers = _all_papers()
        paper = next((item for item in papers if str(item.get("id") or "") == paper_id), None)
        if not paper:
            raise KeyError("paper not found")
        public_papers = [_paper_public(item) for item in papers]
    text = _extract_pdf_text_raw(_local_pdf_for_paper(paper), paper_id=paper_id)
    parsed = _parse_references_from_text(text)
    refs = []
    for idx, raw in enumerate(parsed, start=1):
        raw = _trim_reference_entry(raw)
        if _looks_like_reference_fragment(raw):
            continue
        title = _guess_ref_title(raw)
        if not title or _looks_like_bad_ref_title(title):
            continue
        match = _match_registered_paper(title, raw, public_papers)
        refs.append({
            "ref_id": f"ref-{_slugify(paper_id)}-{len(refs) + 1:03d}",
            "paper_id": paper_id,
            "index": len(refs) + 1,
            "raw": raw,
            "title": title,
            "registered_paper_id": match.get("id", "") if match else "",
            "registered_title": match.get("title", "") if match else "",
            "created_by": actor,
            "created_at": _now(),
        })
    with LOCK:
        _write_store_refs(paper_id, refs)
    return {"refs": refs, "count": len(refs)}


def _write_store_refs(paper_id: str, refs: list[dict]) -> None:
    paper = _paper_by_id(paper_id)
    refs_path = _paper_store_file(paper or {}, "refs") if paper else None
    if not refs_path:
        return
    refs_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(refs_path, refs)


def register_ref(payload: dict, actor: str) -> dict:
    ref_id = str(payload.get("ref_id") or "").strip()
    source_paper_id = str(payload.get("paper_id") or "").strip()
    if not ref_id:
        raise ValueError("ref_id required")
    with LOCK:
        source_paper, ref, refs = _ref_context(ref_id, source_paper_id)
        papers = _all_papers()
        existing_id = str(ref.get("registered_paper_id") or "").strip()
        existing = next((paper for paper in papers if str(paper.get("id") or "") == existing_id), None)
        if existing:
            return {"ref": ref, "paper": _paper_public(existing), "detail": get_paper_detail(existing["id"])}
    metadata = _lookup_ref_metadata(ref)
    if not metadata:
        raise ValueError("Could not locate this reference on arXiv")
    with LOCK:
        papers = _all_papers()
        existing = _find_existing_paper_for_ref(metadata, papers)
        if existing:
            paper = existing
        else:
            paper = PAPER_STORE.register(_paper_payload_from_ref_metadata(metadata), actor)
        updated_ref = None
        now = _now()
        for idx, item in enumerate(refs):
            if str(item.get("ref_id") or "") == ref_id:
                updated_ref = {
                    **item,
                    "title": str(item.get("title") or metadata.get("title") or "").strip(),
                    "registered_paper_id": paper["id"],
                    "registered_title": paper["title"],
                    "registered_by": actor,
                    "registered_at": now,
                    "lookup_source": metadata.get("source_url") or "",
                }
                refs[idx] = updated_ref
                break
        if not updated_ref:
            raise KeyError("ref not found")
        _write_store_refs(str(source_paper.get("id") or updated_ref.get("paper_id") or ""), refs)
    return {"ref": updated_ref, "paper": _paper_public(paper), "detail": get_paper_detail(paper["id"])}


def _paper_payload_from_ref_metadata(metadata: dict) -> dict:
    arxiv_id = str(metadata.get("arxiv_id") or "").strip()
    tags = ["reference"]
    if arxiv_id:
        tags.append("arxiv")
    return {
        "title": metadata.get("title") or "Untitled reference",
        "authors": metadata.get("authors") or "",
        "venue": "arXiv" if arxiv_id else "Reference",
        "year": metadata.get("year") or "",
        "session": "References",
        "abstract": metadata.get("abstract") or "",
        "tags": tags,
        "pdf_path": metadata.get("pdf_url") or "",
        "status": "registered_from_ref",
    }


def _find_existing_paper_for_ref(metadata: dict, papers: list[dict]) -> dict | None:
    title = str(metadata.get("title") or "").casefold()
    arxiv_id = str(metadata.get("arxiv_id") or "").casefold()
    for paper in papers:
        hay = "\n".join(str(paper.get(key) or "") for key in ("id", "slug", "title", "abstract", "pdf_path")).casefold()
        if arxiv_id and arxiv_id in hay:
            return paper
        ptitle = str(paper.get("title") or "").casefold()
        if title and (title == ptitle or title in ptitle or ptitle in title):
            return paper
    return None

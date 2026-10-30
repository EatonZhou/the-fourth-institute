"""Enriching a DBLP entry of a CCF venue: OpenAlex, Semantic Scholar and the paper:ccf Resident."""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlencode
import urllib.request

from management_common.file_lock import lock_stream
from management_portal.paper_records import _now, _slugify
from management_portal.paper_resident import (
    _clean_summary_paragraph, _parse_json_object_from_text, _resident_reply,
)
from management_portal.paper_store import PAPER_STORE_ROOT, _load_json, _notify_paper_projection, _write_json


LOGGER = logging.getLogger(__name__)
# What a lookup of a remote catalog raises: the connection or HTTP status
# (OSError, with URLError and HTTPError), a broken response, a body that is not JSON.
REMOTE_LOOKUP_FAILURES = (OSError, http.client.HTTPException, ValueError)

CCF_DBLP_CACHE_ROOT = PAPER_STORE_ROOT / "_work" / "ccf_dblp"
CCF_ENRICHMENT_CACHE_ROOT = CCF_DBLP_CACHE_ROOT / "_enrichment"


def _http_get_text(url: str, *, timeout: int = 90) -> str:
    req = urllib.request.Request(str(url or ""), headers={"User-Agent": "TFIPortalPaper/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _ccf_paper_identity(paper: dict) -> str:
    parts = [
        str(paper.get("dblp_key") or "").strip(),
        str(paper.get("doi") or "").strip(),
        str(paper.get("dblp_url") or "").strip(),
        str(paper.get("ee") or "").strip(),
        str(paper.get("title") or "").strip(),
    ]
    return "\n".join(part for part in parts if part).casefold()


def _ccf_enrichment_cache_path(paper: dict) -> Path:
    title = str(paper.get("title") or "paper").strip() or "paper"
    digest = hashlib.sha1(_ccf_paper_identity(paper).encode("utf-8", errors="ignore")).hexdigest()[:14]
    return CCF_ENRICHMENT_CACHE_ROOT / f"{_slugify(title)[:70]}-{digest}.json"


def _write_ccf_enrichment_state(
    cache_path: Path,
    payload: dict,
    *,
    actor: object = "",
) -> None:
    _write_json(cache_path, payload)
    _notify_paper_projection(
        cache_path.stem,
        fields=["ccf_enrichment"],
    )


def _read_ccf_enrichment(paper: dict) -> dict | None:
    path = _ccf_enrichment_cache_path(paper)
    data = _load_json(path, {})
    return data if isinstance(data, dict) and data else None


def _abstract_from_openalex_inverted_index(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for word, indexes in value.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            try:
                positioned.append((int(index), str(word)))
            except (TypeError, ValueError, OverflowError):
                continue
    return " ".join(word for _, word in sorted(positioned)).strip()


def _title_similarity(a: str, b: str) -> float:
    def words(text: str) -> set[str]:
        return {item for item in re.findall(r"[a-z0-9]{3,}", str(text or "").casefold())}

    left = words(a)
    right = words(b)
    if not left or not right:
        return 0.0
    if re.sub(r"\W+", "", str(a).casefold()) == re.sub(r"\W+", "", str(b).casefold()):
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _fetch_openalex_ccf_candidate(paper: dict) -> dict | None:
    title = str(paper.get("title") or "").strip()
    doi = str(paper.get("doi") or "").strip()
    urls = []
    if doi:
        urls.append("https://api.openalex.org/works?" + urlencode({"filter": f"doi:{doi}", "per-page": "3"}))
    if title:
        urls.append("https://api.openalex.org/works?" + urlencode({"search": title, "per-page": "5"}))
    for url in urls:
        try:
            payload = json.loads(_http_get_text(url, timeout=48))
        except REMOTE_LOOKUP_FAILURES as exc:
            LOGGER.warning("OpenAlex lookup failed: %s: %s", type(exc).__name__, exc)
            continue
        results = payload.get("results") if isinstance(payload, dict) else []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            item_title = str(item.get("title") or "").strip()
            if title and item_title and _title_similarity(title, item_title) < 0.45:
                continue
            authorships = item.get("authorships") if isinstance(item.get("authorships"), list) else []
            authors = []
            for authorship in authorships[:12]:
                author = authorship.get("author") if isinstance(authorship, dict) else {}
                name = str((author or {}).get("display_name") or "").strip()
                if name:
                    authors.append(name)
            abstract = _abstract_from_openalex_inverted_index(item.get("abstract_inverted_index"))
            return {
                "source": "OpenAlex",
                "title": item_title,
                "abstract": abstract,
                "year": item.get("publication_year") or "",
                "venue": ((item.get("primary_location") or {}).get("source") or {}).get("display_name") if isinstance(item.get("primary_location"), dict) else "",
                "url": item.get("id") or item.get("doi") or "",
                "authors": ", ".join(authors),
            }
    return None


def _fetch_semantic_scholar_ccf_candidate(paper: dict) -> dict | None:
    title = str(paper.get("title") or "").strip()
    if not title:
        return None
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urlencode({
        "query": title,
        "limit": "5",
        "fields": "title,abstract,authors,year,venue,url,externalIds",
    })
    try:
        payload = json.loads(_http_get_text(url, timeout=48))
    except REMOTE_LOOKUP_FAILURES as exc:
        LOGGER.warning("Semantic Scholar lookup failed: %s: %s", type(exc).__name__, exc)
        return None
    results = payload.get("data") if isinstance(payload, dict) else []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        item_title = str(item.get("title") or "").strip()
        if item_title and _title_similarity(title, item_title) < 0.45:
            continue
        authors = item.get("authors") if isinstance(item.get("authors"), list) else []
        return {
            "source": "Semantic Scholar",
            "title": item_title,
            "abstract": str(item.get("abstract") or "").strip(),
            "year": item.get("year") or "",
            "venue": item.get("venue") or "",
            "url": item.get("url") or "",
            "authors": ", ".join(str(author.get("name") or "").strip() for author in authors[:12] if isinstance(author, dict) and author.get("name")),
        }
    return None


def _fetch_ccf_enrichment_sources(paper: dict) -> list[dict]:
    candidates = []
    for fetcher in (_fetch_openalex_ccf_candidate, _fetch_semantic_scholar_ccf_candidate):
        item = fetcher(paper)
        if item and any(str(item.get(key) or "").strip() for key in ("abstract", "url", "venue")):
            candidates.append(item)
    seen = set()
    unique = []
    for item in candidates:
        key = (str(item.get("source") or ""), str(item.get("title") or ""), str(item.get("url") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _generate_ccf_enrichment_summary(paper: dict, candidates: list[dict], *, actor: str) -> tuple[str, dict]:
    source_lines = []
    for item in candidates:
        source_lines.append(
            f"Source: {item.get('source') or ''}\n"
            f"Title: {item.get('title') or ''}\n"
            f"Venue/Year: {item.get('venue') or ''} {item.get('year') or ''}\n"
            f"URL: {item.get('url') or ''}\n"
            f"Abstract: {item.get('abstract') or ''}"
        )
    prompt = f"""请用中文写一段 80-180 字总结，说明这篇论文大概做什么、核心问题和可能的方法方向。不要使用 Markdown、标题或列表。如果检索材料缺少摘要，只能根据标题和来源做保守概括，并明确“不确定细节需要读原文确认”。

DBLP metadata:
Title: {paper.get('title') or ''}
Authors: {paper.get('authors') or ''}
Venue/Year: {paper.get('venue') or paper.get('booktitle') or ''} {paper.get('year') or ''}
DOI: {paper.get('doi') or ''}

Online metadata:
{chr(10).join(source_lines) if source_lines else 'No online metadata found.'}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:ccf")
    return _clean_summary_paragraph(content), usage


def _generate_ccf_web_investigation(paper: dict, candidates: list[dict], *, actor: str) -> dict:
    clean_sources = []
    source_lines = []
    for item in candidates[:8]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        abstract = str(item.get("abstract") or "").strip()
        source = str(item.get("source") or "").strip()
        venue = str(item.get("venue") or "").strip()
        year = str(item.get("year") or "").strip()
        if not any((title, url, abstract, venue)):
            continue
        clean_sources.append({
            "title": title or url or source,
            "url": url,
            "type": source or "metadata",
            "note": "metadata source",
        })
        source_lines.append(
            f"Source: {source}\n"
            f"Title: {title}\n"
            f"Venue/Year: {venue} {year}\n"
            f"URL: {url}\n"
            f"Abstract: {abstract}"
        )
    if not source_lines:
        return {
            "summary_zh": "当前只从 DBLP 找到题名、作者和发表信息，未找到可用于进一步概括的摘要或开放元数据。",
            "findings": ["没有调用 AI；本次结果只基于本地 DBLP 条目和可用公开元数据。"],
            "sources": [],
            "confidence": "low",
            "usage": {"source": "metadata-only"},
        }
    prompt = f"""请根据下面的 DBLP 条目和在线元数据，返回 JSON 对象，不要 Markdown。

JSON schema:
{{
  "summary_zh": "中文一段话，120-260 字，说明论文大概做什么、核心问题和可能方法。",
  "findings": ["2-5 条中文要点，只能来自给定元数据"],
  "confidence": "low|medium|high"
}}

要求：
- 不要调用外部搜索，不要声称已经阅读原文。
- 如果材料只有题名或摘要不足，明确写出不确定性。
- 不要把相似标题的其他论文混进来。

DBLP metadata:
Title: {paper.get('title') or ''}
Authors: {paper.get('authors') or ''}
Venue/Year: {paper.get('venue') or paper.get('booktitle') or ''} {paper.get('year') or ''}
DOI: {paper.get('doi') or ''}

Online metadata:
{chr(10).join(source_lines)}
"""
    content, usage = _resident_reply(prompt, actor=actor, resident_scope="paper:ccf")
    parsed = _parse_json_object_from_text(content)
    findings = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
    return {
        "summary_zh": _clean_summary_paragraph(str(parsed.get("summary_zh") or "")),
        "findings": [str(item).strip() for item in findings if str(item or "").strip()][:8],
        "sources": clean_sources,
        "confidence": str(parsed.get("confidence") or "").strip(),
        "usage": {"source": "kimi_metadata", **(usage if isinstance(usage, dict) else {})},
    }


def _run_ccf_enrichment_job(cache_path: Path, paper: dict, actor: str = "", *, include_web: bool = False) -> None:
    try:
        current = _load_json(cache_path, {})
        _write_ccf_enrichment_state(cache_path, {
            **(current if isinstance(current, dict) else {}),
            "status": "running",
            "updated_at": _now(),
        }, actor=actor)
        candidates = _fetch_ccf_enrichment_sources(paper)
        abstract = next((str(item.get("abstract") or "").strip() for item in candidates if str(item.get("abstract") or "").strip()), "")
        summary, usage = _generate_ccf_enrichment_summary(paper, candidates, actor=actor)
        fast_payload = {
            "status": "web_pending" if include_web else "done",
            "web_status": "pending" if include_web else "",
            "updated_at": _now(),
            "actor": actor,
            "paper_key": _ccf_paper_identity(paper),
            "abstract": abstract,
            "summary_zh": summary,
            "sources": candidates,
            "usage": usage,
        }
        current_request = _load_json(cache_path, {})
        fast_payload = {
            **(
                current_request
                if isinstance(current_request, dict)
                else {}
            ),
            **fast_payload,
        }
        web_requested = include_web or bool((current_request if isinstance(current_request, dict) else {}).get("requested_web"))
        if web_requested:
            fast_payload["status"] = "web_pending"
            fast_payload["web_status"] = "pending"
            fast_payload["requested_web"] = True
        _write_ccf_enrichment_state(cache_path, fast_payload, actor=actor)
        if not web_requested:
            return
        try:
            web = _generate_ccf_web_investigation(paper, candidates, actor=actor)
            final_payload = {
                **fast_payload,
                "status": "done",
                "web_status": "done",
                "requested_web": False,
                "updated_at": _now(),
                "web_summary_zh": str(web.get("summary_zh") or "").strip(),
                "web_findings": web.get("findings") if isinstance(web.get("findings"), list) else [],
                "web_sources": web.get("sources") if isinstance(web.get("sources"), list) else [],
                "web_confidence": str(web.get("confidence") or "").strip(),
                "web_usage": web.get("usage") if isinstance(web.get("usage"), dict) else {},
            }
            _write_ccf_enrichment_state(cache_path, final_payload, actor=actor)
        except Exception as exc:
            error_payload = {
                **fast_payload,
                "status": "done",
                "web_status": "error",
                "requested_web": False,
                "updated_at": _now(),
                "web_error": str(exc),
            }
            _write_ccf_enrichment_state(cache_path, error_payload, actor=actor)
    except Exception as exc:
        current = _load_json(cache_path, {})
        current.update({
            "status": "error",
            "updated_at": _now(),
            "error": str(exc),
            "paper_key": _ccf_paper_identity(paper),
        })
        _write_ccf_enrichment_state(cache_path, current, actor=actor)


def _run_ccf_web_enrichment_job(cache_path: Path, paper: dict, actor: str = "") -> None:
    current = _load_json(cache_path, {})
    if not (
        str(current.get("summary_zh") or "").strip()
        or str(current.get("abstract") or "").strip()
    ):
        _run_ccf_enrichment_job(
            cache_path,
            paper,
            actor,
            include_web=True,
        )
        return
    candidates = (
        current.get("sources")
        if isinstance(current.get("sources"), list)
        else []
    )
    pending = {
        **current,
        "status": "web_pending",
        "web_status": "pending",
        "updated_at": _now(),
        "actor": actor,
        "paper_key": _ccf_paper_identity(paper),
    }
    _write_ccf_enrichment_state(cache_path, pending, actor=actor)
    try:
        web = _generate_ccf_web_investigation(paper, candidates, actor=actor)
        final_payload = {
            **pending,
            "status": "done",
            "web_status": "done",
            "requested_web": False,
            "updated_at": _now(),
            "web_summary_zh": str(web.get("summary_zh") or "").strip(),
            "web_findings": (
                web.get("findings")
                if isinstance(web.get("findings"), list)
                else []
            ),
            "web_sources": (
                web.get("sources")
                if isinstance(web.get("sources"), list)
                else []
            ),
            "web_confidence": str(
                web.get("confidence") or ""
            ).strip(),
            "web_usage": (
                web.get("usage")
                if isinstance(web.get("usage"), dict)
                else {}
            ),
        }
        _write_ccf_enrichment_state(cache_path, final_payload, actor=actor)
    except Exception as exc:
        error_payload = {
            **pending,
            "status": "done",
            "web_status": "error",
            "requested_web": False,
            "updated_at": _now(),
            "web_error": str(exc),
        }
        _write_ccf_enrichment_state(cache_path, error_payload, actor=actor)


def _ccf_enrichment_request_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".request.json")


def _run_ccf_enrichment_request(request_path: Path) -> None:
    request_path = Path(request_path).resolve(strict=False)
    root = CCF_ENRICHMENT_CACHE_ROOT.resolve(strict=False)
    if root not in request_path.parents:
        raise ValueError("CCF enrichment request is outside its work root")
    request = _load_json(request_path, {})
    paper = (
        dict(request.get("paper"))
        if isinstance(request.get("paper"), dict)
        else {}
    )
    if not str(paper.get("title") or "").strip():
        raise ValueError("CCF enrichment request has no paper")
    cache_path = Path(str(request.get("cache_path") or "")).resolve(
        strict=False
    )
    expected = _ccf_enrichment_cache_path(paper).resolve(strict=False)
    if cache_path != expected:
        raise ValueError("CCF enrichment request cache identity mismatch")
    actor = str(request.get("actor") or "")
    if bool(request.get("web_only")):
        _run_ccf_web_enrichment_job(cache_path, paper, actor)
    else:
        _run_ccf_enrichment_job(
            cache_path,
            paper,
            actor,
            include_web=bool(request.get("include_web")),
        )


def _start_ccf_enrichment_task(
    cache_path: Path,
    paper: dict,
    actor: str,
    *,
    include_web: bool,
    web_only: bool,
) -> dict:
    from management_portal import business_tasks

    request_path = _ccf_enrichment_request_path(cache_path)
    _write_json(request_path, {
        "cache_path": str(cache_path),
        "paper": dict(paper),
        "actor": str(actor or ""),
        "include_web": bool(include_web),
        "web_only": bool(web_only),
        "created_at": _now(),
    })
    task_id = f"paper-ccf:{_ccf_paper_identity(paper)[:160]}"
    business_tasks.start(
        task_id,
        _run_ccf_enrichment_request,
        request_path,
    )
    return {"business_task_id": task_id}


def _ccf_enrichment_task_active(cached: dict) -> bool:
    from management_portal import business_tasks

    return business_tasks.is_active(
        str(cached.get("business_task_id") or "")
    )


def ccf_paper_enrichment(payload: dict, actor: str = "") -> dict:
    paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
    if not isinstance(paper, dict) or not str(paper.get("title") or "").strip():
        raise ValueError("paper title required")
    want_web = str(payload.get("web") or "").strip().lower() in {"1", "true", "yes", "on"} if isinstance(payload, dict) else False
    cache_path = _ccf_enrichment_cache_path(paper)
    lock_path = cache_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file, lock_stream(lock_file):
        cached = _load_json(cache_path, {})
        status = str(cached.get("status") or "").strip()
        web_status = str(cached.get("web_status") or "").strip()
        if not want_web and status == "done":
            return cached
        if want_web and status == "done" and web_status == "done":
            return cached
        stale = (
            _now()
            - int(
                cached.get("updated_at")
                or cached.get("started_at")
                or 0
            )
            > 20 * 60
        )
        active_statuses = {"pending", "running", "web_pending"}
        if status in active_statuses and (
            not stale or _ccf_enrichment_task_active(cached)
        ):
            if not want_web:
                return cached
            pending = {
                **(cached if isinstance(cached, dict) else {}),
                "status": "web_pending",
                "web_status": "pending",
                "requested_web": True,
                "updated_at": _now(),
                "actor": actor,
                "paper_key": _ccf_paper_identity(paper),
            }
            _write_ccf_enrichment_state(cache_path, pending, actor=actor)
            return pending
        pending = {
            **(cached if isinstance(cached, dict) else {}),
            "status": (
                "web_pending"
                if want_web and bool(cached)
                else "pending"
            ),
            "web_status": "pending" if want_web else web_status,
            "requested_web": bool(want_web),
            "started_at": _now(),
            "updated_at": _now(),
            "actor": actor,
            "paper_key": _ccf_paper_identity(paper),
            "error": "",
        }
        _write_ccf_enrichment_state(cache_path, pending, actor=actor)
        try:
            execution = _start_ccf_enrichment_task(
                cache_path,
                dict(paper),
                actor,
                include_web=want_web,
                web_only=bool(
                    want_web
                    and (
                        str(cached.get("summary_zh") or "").strip()
                        or str(cached.get("abstract") or "").strip()
                    )
                ),
            )
        except Exception as exc:
            failed = {
                **pending,
                "status": "error",
                "updated_at": _now(),
                "error": (
                    "Failed to start CCF enrichment business task: "
                    f"{exc}"
                ),
            }
            _write_ccf_enrichment_state(cache_path, failed, actor=actor)
            return failed
        accepted = {**pending, **execution}
        _write_ccf_enrichment_state(cache_path, accepted, actor=actor)
        return accepted

"""CCF venues: the CCF catalog, DBLP entries per venue and year, and registering a DBLP paper."""

from __future__ import annotations

from datetime import datetime
import html as html_lib
import os
from pathlib import Path
import re
import time

from management_common.limit_contract import normalize_optional_limit
from management_portal import paper_state_store
from management_portal.paper_ccf_enrichment import CCF_DBLP_CACHE_ROOT, _http_get_text, _read_ccf_enrichment
from management_portal.paper_records import _normalize_list, _now, _slugify
from management_portal.paper_registration import register_paper
from management_portal.paper_search import resource_index_papers
from management_portal.paper_store import PAPER_STORE_ROOT, _load_json, _write_json


CCF_CATALOG_CACHE_PATH = PAPER_STORE_ROOT / "_index" / "ccf_catalog_v7.json"


CCF_REFRESH_REPORT_PATH = CCF_DBLP_CACHE_ROOT / "refresh-report.json"
CCF_CATALOG_URL = os.getenv("PAPER_CCF_CATALOG_URL", "https://ccf.atom.im/")


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _ccf_cache_fresh(path: Path, max_age_seconds: int = 30 * 24 * 60 * 60) -> bool:
    try:
        return path.is_file() and _now() - int(path.stat().st_mtime) < max_age_seconds
    except OSError:  # removed between the two calls, or unreadable
        return False


def _parse_ccf_catalog_html(html: str) -> list[dict]:
    venues: list[dict] = []
    rows = re.findall(r"<tr[^>]*class=[\"'][^\"']*\bitem\b[^\"']*[\"'][^>]*>(.*?)</tr>", str(html or ""), re.S | re.I)
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 6:
            continue
        title_html = cells[2]
        rank = _strip_html(cells[3]).upper()
        kind = _strip_html(cells[4])
        if kind not in {"会议", "期刊"} or rank not in {"A", "B"}:
            continue
        href_match = re.search(r"<a[^>]+href=[\"']([^\"']+)[\"']", title_html, re.I)
        dblp_url = html_lib.unescape(href_match.group(1)).strip() if href_match else ""
        dblp_match = re.search(r"/db/(conf|journals)/([^/]+)/", dblp_url)
        if not dblp_match:
            continue
        dblp_url = dblp_url.replace("http://dblp.uni-trier.de", "https://dblp.org").replace("https://dblp.uni-trier.de", "https://dblp.org")
        abbr = _strip_html(cells[1])
        venues.append({
            "abbr": abbr,
            "short_name": abbr,
            "name": _strip_html(title_html),
            "rank": rank,
            "type": kind,
            "category": _strip_html(cells[5]),
            "dblp_url": dblp_url,
            "dblp_section": dblp_match.group(1).strip(),
            "dblp_conf_key": dblp_match.group(2).strip(),
            "source": "CCF recommended list v7 / 2026",
        })
    venues.sort(key=lambda item: (str(item.get("rank") or ""), str(item.get("category") or ""), str(item.get("abbr") or "")))
    return venues


def _load_ccf_catalog_cache() -> list[dict]:
    cached = _load_json(CCF_CATALOG_CACHE_PATH, {})
    venues = cached.get("venues") if isinstance(cached, dict) else []
    return [item for item in venues if isinstance(item, dict)] if isinstance(venues, list) else []


def _load_curated_venues() -> list[dict]:
    out = []
    for item in paper_state_store.curated_venues():
        if not isinstance(item, dict):
            continue
        conf_key = str(item.get("dblp_conf_key") or "").strip()
        section = str(item.get("dblp_section") or "conf").strip() or "conf"
        if not conf_key:
            continue
        venue = {
            **item,
            "abbr": str(item.get("abbr") or item.get("short_name") or conf_key).strip(),
            "short_name": str(item.get("short_name") or item.get("abbr") or conf_key).strip(),
            "name": str(item.get("name") or item.get("abbr") or conf_key).strip(),
            "rank": str(item.get("rank") or "Curated").strip() or "Curated",
            "category": str(item.get("category") or "芯片电路").strip() or "芯片电路",
            "type": str(item.get("type") or "会议").strip() or "会议",
            "dblp_section": section,
            "dblp_conf_key": conf_key,
            "dblp_url": str(item.get("dblp_url") or f"https://dblp.org/db/{section}/{conf_key}/").strip(),
            "source": str(item.get("source") or "TFI Portal curated chip/circuit venue list").strip(),
            "curated": True,
        }
        out.append(venue)
    return out


def _merge_curated_venues(venues: list[dict]) -> list[dict]:
    merged = [dict(item) for item in venues if isinstance(item, dict)]
    by_key = {str(item.get("dblp_conf_key") or ""): idx for idx, item in enumerate(merged)}
    for curated in _load_curated_venues():
        key = str(curated.get("dblp_conf_key") or "")
        if key and key in by_key:
            existing = dict(merged[by_key[key]])
            existing["curated"] = True
            existing["curated_category"] = str(curated.get("category") or "")
            existing["curated_reason"] = str(curated.get("curated_reason") or "")
            existing["curated_source"] = str(curated.get("source") or "")
            merged[by_key[key]] = existing
        else:
            merged.append(curated)
    merged.sort(key=lambda item: (str(item.get("rank") or ""), str(item.get("category") or ""), str(item.get("abbr") or "")))
    return merged


def _ccf_catalog(force: bool = False, *, allow_network: bool = True) -> list[dict]:
    if not force and _ccf_cache_fresh(CCF_CATALOG_CACHE_PATH):
        cached = _load_json(CCF_CATALOG_CACHE_PATH, {})
        venues = cached.get("venues") if isinstance(cached, dict) else []
        if (
            isinstance(venues, list)
            and all("short_name" in item for item in venues if isinstance(item, dict))
            and any(str(item.get("type") or "") == "期刊" for item in venues if isinstance(item, dict))
        ):
            return _merge_curated_venues([item for item in venues if isinstance(item, dict)])
    if not allow_network:
        return _merge_curated_venues(_load_ccf_catalog_cache())
    html = _http_get_text(CCF_CATALOG_URL, timeout=135)
    venues = _parse_ccf_catalog_html(html)
    CCF_CATALOG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CCF_CATALOG_CACHE_PATH, {
        "schema": "tfi.paper.ccf_catalog.v1",
        "source_url": CCF_CATALOG_URL,
        "source_version": "CCF recommended list v7 / 2026",
        "fetched_at": _now(),
        "venues": venues,
    })
    return _merge_curated_venues(venues)


def ccf_venues(params: dict | None = None) -> dict:
    params = params or {}
    rank = str(params.get("rank") or "").strip().upper()
    force = str(params.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
    venues = _ccf_catalog(force=force, allow_network=force)
    if rank in {"A", "B", "CURATED"}:
        target_rank = "Curated" if rank == "CURATED" else rank
        venues = [item for item in venues if str(item.get("rank") or "").upper() == target_rank.upper()]
    curated_count = sum(1 for item in venues if item.get("curated"))
    return {
        "summary": {
            "source_version": "CCF recommended list v7 / 2026 + TFI Portal curated chip/circuit venues",
            "venue_count": len(venues),
            "rank_a_count": sum(1 for item in venues if str(item.get("rank") or "").upper() == "A"),
            "rank_b_count": sum(1 for item in venues if str(item.get("rank") or "").upper() == "B"),
            "curated_count": curated_count,
        },
        "venues": venues,
    }


def _ccf_venue_by_key(conf_key: str) -> dict | None:
    key = str(conf_key or "").strip()
    return next((item for item in _ccf_catalog(allow_network=False) if str(item.get("dblp_conf_key") or "") == key), None) if key else None


def _ccf_cache_path(kind: str, conf_key: str, suffix: str = "") -> Path:
    tail = f"-{_slugify(suffix)}" if suffix else ""
    return CCF_DBLP_CACHE_ROOT / _slugify(conf_key) / f"{kind}{tail}.json"


def _ccf_entry_year(entry: dict | None) -> int:
    if not isinstance(entry, dict):
        return 0
    try:
        year = int(entry.get("year") or 0)
        if year:
            return year
    except (TypeError, ValueError):
        pass
    text = " ".join(str(entry.get(key) or "") for key in ("entry_id", "label", "xml_url"))
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    return int(match.group(1)) if match else 0


def _normalize_ccf_paper_ranks(value) -> set[str]:
    if value is None:
        return {"A"}
    if isinstance(value, str):
        tokens = re.split(r"[\s,;]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        tokens = [str(item or "") for item in value]
    else:
        tokens = [str(value or "")]
    ranks = {token.strip().upper() for token in tokens if token.strip()}
    if not ranks:
        return {"A"}
    if ranks & {"*", "ALL"}:
        return set()
    return {rank for rank in ranks if rank in {"A", "B", "CURATED"}}


def _dblp_entries_for_venue(venue: dict, *, force: bool = False, allow_network: bool = True) -> list[dict]:
    from management_portal import paper_dblp_metadata

    conf_key = str(venue.get("dblp_conf_key") or "").strip()
    cache_path = _ccf_cache_path("years", conf_key)
    if not force and cache_path.is_file():
        cached = _load_json(cache_path, {})
        years = cached.get("years") if isinstance(cached, dict) else []
        if isinstance(years, list) and years:
            return [item for item in years if isinstance(item, dict)]
    if not allow_network:
        return []
    years = paper_dblp_metadata.catalog_entries(venue)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(cache_path, {"fetched_at": _now(), "conf_key": conf_key, "section": venue.get("dblp_section") or "conf", "years": years})
    return years


def ccf_years(params: dict | None = None) -> dict:
    params = params or {}
    conf_key = str(params.get("venue") or params.get("conf") or "").strip()
    if not conf_key:
        raise ValueError("venue required")
    venue = _ccf_venue_by_key(conf_key)
    force = str(params.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not venue and force:
        venue = next((item for item in _ccf_catalog(force=True, allow_network=True) if str(item.get("dblp_conf_key") or "") == conf_key), None)
    if not venue:
        raise KeyError("CCF venue not found")
    return {"venue": venue, "years": _dblp_entries_for_venue(venue, force=force, allow_network=force)}


def _paper_doi_from_url(value: str) -> str:
    match = re.search(r"doi\.org/(.+)$", str(value or ""), re.I)
    return match.group(1).strip() if match else ""


def _registered_paper_index(papers: list[dict]) -> dict:
    index = {"tokens": {}, "titles": {}, "title_ngrams": {}, "titles_by_id": {}}
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id") or "")
        if not paper_id:
            continue
        identifiers = paper.get("identifiers") if isinstance(paper.get("identifiers"), dict) else {}
        fields = [paper.get(key) for key in ("id", "slug", "title", "pdf_path", "analysis_path")]
        fields.extend(identifiers.values())
        values = [str(value or "").strip() for value in fields if str(value or "").strip()]
        title_key = re.sub(r"\W+", "", str(paper.get("title") or "").casefold())
        if title_key:
            index["titles"].setdefault(title_key, paper_id)
            index["titles_by_id"][paper_id] = title_key
            if len(title_key) >= 24:
                for offset in range(len(title_key) - 23):
                    gram = title_key[offset:offset + 24]
                    index["title_ngrams"].setdefault(gram, set()).add(paper_id)
        for value in values:
            token = value.casefold()
            index["tokens"].setdefault(token, paper_id)
            doi = _paper_doi_from_url(value)
            if doi:
                index["tokens"].setdefault(doi.casefold(), paper_id)
    return index


def _registered_paper_for_dblp(item: dict, registered_index: dict) -> str:
    title = re.sub(r"\W+", "", str(item.get("title") or "").casefold())
    doi = str(item.get("doi") or "").casefold()
    dblp_key = str(item.get("dblp_key") or "").casefold()
    url = str(item.get("dblp_url") or "").casefold()
    tokens = registered_index.get("tokens") if isinstance(registered_index, dict) else {}
    for token in (doi, dblp_key, url):
        if token and token in tokens:
            return str(tokens[token] or "")
    exact = registered_index.get("titles", {}).get(title)
    if exact:
        return str(exact)
    candidate_ids = set()
    if len(title) >= 24:
        title_ngrams = registered_index.get("title_ngrams", {})
        for offset in range(len(title) - 23):
            candidate_ids.update(title_ngrams.get(title[offset:offset + 24], ()))
    titles_by_id = registered_index.get("titles_by_id", {})
    for paper_id in candidate_ids:
        ptitle = str(titles_by_id.get(paper_id) or "")
        shorter = min(len(title), len(ptitle)) if title and ptitle else 0
        longer = max(len(title), len(ptitle)) if title and ptitle else 0
        near_contained = (
            shorter >= 40
            and longer > 0
            and shorter / longer >= 0.75
            and (title in ptitle or ptitle in title)
        )
        if title and ptitle and (title == ptitle or near_contained):
            return str(paper_id or "")
    return ""


def ccf_papers_cache(params: dict | None = None) -> dict:
    params = params or {}
    conf_key = str(params.get("venue") or params.get("conf") or "").strip()
    year_text = str(params.get("year") or "").strip()
    if not conf_key or not year_text:
        raise ValueError("venue and year required")
    venue = _ccf_venue_by_key(conf_key)
    force = str(params.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not venue and force:
        venue = next((item for item in _ccf_catalog(force=True, allow_network=True) if str(item.get("dblp_conf_key") or "") == conf_key), None)
    if not venue:
        raise KeyError("CCF venue not found")
    cache_path = _ccf_cache_path("papers", conf_key, year_text)
    papers = None
    entry = None
    if not force and _ccf_cache_fresh(cache_path):
        cached = _load_json(cache_path, {})
        papers = cached.get("papers") if isinstance(cached, dict) else None
        entry = cached.get("entry") if isinstance(cached, dict) and isinstance(cached.get("entry"), dict) else None
    if isinstance(papers, list) and entry is None:
        years = _dblp_entries_for_venue(venue, allow_network=False)
        entry = next((
            item for item in years
            if str(item.get("entry_id") or "") == year_text
            or str(item.get("year") or "") == year_text
            or str(item.get("issue") or "") == year_text
        ), None)
    if not isinstance(papers, list):
        if not force:
            years = _dblp_entries_for_venue(venue, allow_network=False)
            entry = next((
                item for item in years
                if str(item.get("entry_id") or "") == year_text
                or str(item.get("year") or "") == year_text
                or str(item.get("issue") or "") == year_text
            ), None)
            return {
                "venue": venue,
                "year": int((entry or {}).get("year") or 0),
                "issue": str((entry or {}).get("issue") or ""),
                "entry_id": str((entry or {}).get("entry_id") or year_text),
                "label": str((entry or {}).get("label") or year_text),
                "count": 0,
                "papers": [],
                "cache_missing": True,
            }
        years = _dblp_entries_for_venue(venue, force=True, allow_network=True)
        entry = next((
            item for item in years
            if str(item.get("entry_id") or "") == year_text
            or str(item.get("year") or "") == year_text
            or str(item.get("issue") or "") == year_text
        ), None)
        if not entry:
            raise KeyError("CCF venue year or issue not found")
        year = int(entry.get("year") or 0)
        if not year:
            year_match = re.search(r"\b((?:19|20)\d{2})\b", str(entry.get("label") or year_text))
            year = int(year_match.group(1)) if year_match else datetime.now().year
        from management_portal import paper_dblp_metadata

        papers = paper_dblp_metadata.papers_for_entry(venue, entry)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(cache_path, {"fetched_at": _now(), "venue": venue, "entry": entry, "year": year, "metadata_source": "dblp-sparql", "papers": papers})
    return {
        "venue": venue,
        "year": int((entry or {}).get("year") or 0),
        "issue": str((entry or {}).get("issue") or ""),
        "entry_id": str((entry or {}).get("entry_id") or year_text),
        "label": str((entry or {}).get("label") or year_text),
        "count": len(papers),
        "papers": papers,
    }


def ccf_papers(params: dict | None = None) -> dict:
    """Add Portal registration and enrichment only for the interactive page."""
    result = ccf_papers_cache(params)
    if result.get("cache_missing"):
        return result
    papers = result["papers"]
    registered_index = _registered_paper_index(resource_index_papers())
    public_with_order = []
    for order, item in enumerate(papers):
        if isinstance(item, dict):
            registered_id = _registered_paper_for_dblp(item, registered_index)
            public_with_order.append((0 if registered_id else 1, order, {
                **item,
                "registered_paper_id": registered_id,
                "registered": bool(registered_id),
                "enrichment": _read_ccf_enrichment(item) or {},
            }))
    public = [item for _, _, item in sorted(public_with_order, key=lambda row: (row[0], row[1]))]
    return {**result, "count": len(public), "papers": public}


def register_ccf_dblp_paper(payload: dict, actor: str) -> dict:
    paper = payload.get("paper") if isinstance(payload.get("paper"), dict) else payload
    title = str(paper.get("title") or "").strip()
    if not title:
        raise ValueError("title required")
    tags = _normalize_list(["ccf", f"CCF-{str(paper.get('ccf_rank') or '').upper()}", str(paper.get("ccf_category") or "")])
    abstract_lines = [
        str(paper.get("abstract") or "").strip(),
        f"DBLP: {paper.get('dblp_url') or ''}".strip(),
        f"DOI: {paper.get('doi') or ''}".strip(),
        f"CCF: {paper.get('ccf_rank') or ''} {paper.get('ccf_category') or ''}".strip(),
    ]
    return register_paper({
        "title": title,
        "authors": str(paper.get("authors") or ""),
        "venue": str(paper.get("venue") or paper.get("booktitle") or ""),
        "year": str(paper.get("year") or ""),
        "abstract": "\n".join(line for line in abstract_lines if line and not line.endswith(":")),
        "tags": tags,
        "project_ids": payload.get("project_ids") or [],
        "session": str(payload.get("session") or ""),
        "status": "metadata-only",
        "identifiers": {
            "doi": str(paper.get("doi") or ""),
            "dblp": str(paper.get("dblp_key") or ""),
            "dblp_url": str(paper.get("dblp_url") or ""),
        },
    }, actor)


def refresh_ccf_cache(
    *,
    latest_years: int | None = 2,
    min_paper_year: int | None = 2020,
    paper_ranks: str | list[str] | tuple[str, ...] | set[str] | None = "A,Curated",
    max_venues: int | None = None,
    delay_seconds: float = 0.0,
    refresh_catalog: bool = True,
    refresh_existing: bool = True,
) -> dict:
    latest_years = normalize_optional_limit(latest_years, default=2)
    min_paper_year = normalize_optional_limit(
        min_paper_year,
        default=2020,
    )
    auto_paper_ranks = _normalize_ccf_paper_ranks(paper_ranks)
    max_venues = normalize_optional_limit(max_venues)
    delay_seconds = max(0.0, float(delay_seconds or 0.0))
    started_at = _now()
    venues = _ccf_catalog(force=refresh_catalog, allow_network=refresh_catalog)
    venues = sorted(
        venues,
        key=lambda item: (
            0 if str(item.get("dblp_section") or "conf") == "conf" else 1,
            str(item.get("rank") or ""),
            str(item.get("abbr") or item.get("dblp_conf_key") or ""),
        ),
    )
    if max_venues is not None:
        venues = venues[:max_venues]
    report = {
        "schema": "tfi.paper.ccf_refresh.v1",
        "started_at": started_at,
        "finished_at": None,
        "latest_years": latest_years,
        "all_entries": latest_years is None,
        "min_paper_year": min_paper_year,
        "paper_ranks": sorted(auto_paper_ranks) if auto_paper_ranks else ["all"],
        "refresh_catalog": refresh_catalog,
        "refresh_existing": refresh_existing,
        "venue_count": len(venues),
        "year_index_count": 0,
        "paper_year_count": 0,
        "paper_count": 0,
        "skipped_by_rank": 0,
        "skipped_before_min_year": 0,
        "errors": [],
    }
    CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CCF_REFRESH_REPORT_PATH, report)
    for venue in venues:
        conf_key = str(venue.get("dblp_conf_key") or "").strip()
        if not conf_key:
            continue
        try:
            years = _dblp_entries_for_venue(venue, force=refresh_existing, allow_network=True)
            report["year_index_count"] += len(years)
        except Exception as exc:
            report["errors"].append({"venue": conf_key, "stage": "years", "error": str(exc)})
            continue
        selected_entries = (
            years if latest_years is None else years[:latest_years]
        )
        venue_rank = str(venue.get("rank") or "").strip().upper()
        if auto_paper_ranks and venue_rank not in auto_paper_ranks:
            report["skipped_by_rank"] += len(selected_entries)
            CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _write_json(CCF_REFRESH_REPORT_PATH, report)
            continue
        for year_entry in selected_entries:
            entry_id = str(year_entry.get("entry_id") or year_entry.get("year") or year_entry.get("issue") or "").strip()
            if not entry_id:
                continue
            entry_year = _ccf_entry_year(year_entry)
            if (
                min_paper_year is not None
                and (not entry_year or entry_year < min_paper_year)
            ):
                report["skipped_before_min_year"] += 1
                continue
            try:
                cached_payload = ccf_papers_cache({"venue": conf_key, "year": entry_id})
                if cached_payload.get("cache_missing") or refresh_existing:
                    payload = ccf_papers_cache({"venue": conf_key, "year": entry_id, "refresh": "1"})
                else:
                    payload = cached_payload
                report["paper_year_count"] += 1
                report["paper_count"] += int(payload.get("count") or 0)
            except Exception as exc:
                report["errors"].append({"venue": conf_key, "entry_id": entry_id, "stage": "papers", "error": str(exc)})
            if delay_seconds:
                time.sleep(delay_seconds)
            CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _write_json(CCF_REFRESH_REPORT_PATH, report)
        CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_json(CCF_REFRESH_REPORT_PATH, report)
    report["finished_at"] = _now()
    CCF_REFRESH_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_json(CCF_REFRESH_REPORT_PATH, report)
    return report

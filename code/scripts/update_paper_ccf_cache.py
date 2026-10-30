#!/usr/bin/env python3
"""Refresh cached CCF A/B DBLP venue entries for the Paper page."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from management_portal.resident_shell_environment import portal_database_url  # noqa: E402

# The curated venues merged into the CCF catalog are stored in PostgreSQL.
# RunScript starts this script without Portal's service environment.
os.environ.setdefault("PORTAL_DATABASE_URL", portal_database_url())

from management_portal import paper_ccf, paper_ccf_enrichment, paper_records, paper_store  # noqa: E402
from management_common.limit_contract import normalize_optional_limit  # noqa: E402


def _is_retryable_error(error: str) -> bool:
    text = str(error or "").casefold()
    if "404" in text or "not found" in text:
        return False
    return True


def retry_previous_errors(*, delay_seconds: float, retry_all_errors: bool, min_paper_year: int | None, paper_ranks: str) -> dict:
    source_path = paper_ccf.CCF_REFRESH_REPORT_PATH
    retry_path = paper_ccf_enrichment.CCF_DBLP_CACHE_ROOT / "retry-report.json"
    source = json.loads(source_path.read_text(encoding="utf-8")) if source_path.is_file() else {}
    errors = [item for item in source.get("errors", []) if isinstance(item, dict)]
    auto_paper_ranks = paper_ccf._normalize_ccf_paper_ranks(paper_ranks)
    report = {
        "schema": "tfi.paper.ccf_retry.v1",
        "source_report": str(source_path),
        "started_at": paper_records._now(),
        "finished_at": None,
        "source_error_count": len(errors),
        "retried": 0,
        "skipped": 0,
        "skipped_by_rank": 0,
        "min_paper_year": normalize_optional_limit(
            min_paper_year,
            default=2020,
        ),
        "paper_ranks": sorted(auto_paper_ranks) if auto_paper_ranks else ["all"],
        "succeeded": 0,
        "errors": [],
    }
    retry_path.parent.mkdir(parents=True, exist_ok=True)
    paper_store._write_json(retry_path, report)
    for item in errors:
        venue = str(item.get("venue") or "").strip()
        stage = str(item.get("stage") or "").strip()
        entry_id = str(item.get("entry_id") or "").strip()
        error_text = str(item.get("error") or "")
        if not venue or (not retry_all_errors and not _is_retryable_error(error_text)):
            report["skipped"] += 1
            paper_store._write_json(retry_path, report)
            continue
        if stage == "papers" and auto_paper_ranks:
            venue_info = paper_ccf._ccf_venue_by_key(venue) or {}
            rank = str(venue_info.get("rank") or "").strip().upper()
            if rank and rank not in auto_paper_ranks:
                report["skipped"] += 1
                report["skipped_by_rank"] += 1
                paper_store._write_json(retry_path, report)
                continue
        if stage == "papers" and report["min_paper_year"] is not None:
            entry_year = paper_ccf._ccf_entry_year(item)
            if entry_year and entry_year < report["min_paper_year"]:
                report["skipped"] += 1
                paper_store._write_json(retry_path, report)
                continue
        try:
            if stage == "years":
                paper_ccf.ccf_years({"venue": venue, "refresh": "1"})
            elif stage == "papers" and entry_id:
                paper_ccf.ccf_papers_cache({"venue": venue, "year": entry_id, "refresh": "1"})
            else:
                report["skipped"] += 1
                paper_store._write_json(retry_path, report)
                continue
            report["retried"] += 1
            report["succeeded"] += 1
        except Exception as exc:
            report["retried"] += 1
            report["errors"].append({**item, "retry_error": str(exc)})
        paper_store._write_json(retry_path, report)
        if delay_seconds:
            time.sleep(max(0.0, delay_seconds))
    report["finished_at"] = paper_records._now()
    paper_store._write_json(retry_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh CCF A/B DBLP cache for TFI Portal Paper.")
    latest_years = parser.add_mutually_exclusive_group()
    latest_years.add_argument("--latest-years", type=int, default=2, help="Refresh this many latest years or issues per venue.")
    latest_years.add_argument("--all-years", action="store_true", help="Refresh every cached year or issue per venue.")
    paper_years = parser.add_mutually_exclusive_group()
    paper_years.add_argument("--min-paper-year", type=int, default=2020, help="Only fetch paper lists for entries from this year or later.")
    paper_years.add_argument("--all-paper-years", action="store_true", help="Disable the lower paper-year boundary.")
    parser.add_argument("--paper-ranks", default="A,Curated", help="Comma-separated CCF ranks whose paper lists are fetched automatically. Use all to fetch every rank.")
    parser.add_argument("--max-venues", type=int, default=None, help="Limit venues for testing; omit for all venues.")
    parser.add_argument("--delay-seconds", type=float, default=5.0, help="Delay between DBLP requests.")
    parser.add_argument("--no-refresh-catalog", action="store_true", help="Use the local CCF catalog cache instead of refreshing it first.")
    parser.add_argument("--only-missing", action="store_true", help="Do not refresh existing local year or paper caches.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry retryable failures from the last refresh report.")
    parser.add_argument("--retry-all-errors", action="store_true", help="Retry all failures, including 404-style errors.")
    parser.add_argument("--fail-on-errors", action="store_true", help="Exit non-zero when per-venue refresh errors occur.")
    args = parser.parse_args()

    if args.retry_errors:
        report = retry_previous_errors(delay_seconds=args.delay_seconds, retry_all_errors=args.retry_all_errors, min_paper_year=None if args.all_paper_years else args.min_paper_year, paper_ranks=args.paper_ranks)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if args.fail_on_errors and report.get("errors") else 0

    report = paper_ccf.refresh_ccf_cache(
        latest_years=None if args.all_years else args.latest_years,
        min_paper_year=None if args.all_paper_years else args.min_paper_year,
        paper_ranks=args.paper_ranks,
        max_venues=args.max_venues,
        delay_seconds=args.delay_seconds,
        refresh_catalog=not args.no_refresh_catalog,
        refresh_existing=not args.only_missing,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.fail_on_errors and report.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())

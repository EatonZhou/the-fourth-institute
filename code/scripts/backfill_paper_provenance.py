#!/usr/bin/env python3
"""Refresh managed Paper provenance through authenticated APIs, without AI calls.

python scripts/backfill_paper_provenance.py --resident-id <caller> --paper-id <paper>
Add --apply to save; --source-resident-id restricts the original generating Agent.
"""
import argparse
from collections import Counter
import json
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.resident_hatg_instruction import DEFAULT_PORTAL_URL, _resident_id, _token
from management_common.portal_agent_http import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resident-id", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--paper-id", action="append")
    selection.add_argument("--all", action="store_true", help="Inventory the full visible library without starting jobs")
    parser.add_argument("--source-resident-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, help="Save the generated inventory and per-paper results as JSON")
    args = parser.parse_args()
    caller = _resident_id(args.resident_id)
    headers = {"Authorization": "Bearer " + _token(caller), "Content-Type": "application/json"}
    # The caller's Lineage is identity context, never a way to borrow its owner.
    from scripts.resident_hatg_instruction import execute, SCHEMA
    result = execute(caller, {"schema": SCHEMA, "instruction": "GetResident", "arguments": {"resident_id": caller}})
    headers["X-Portal-Lineage-ID"] = result["result"]["resident"]["lineage_id"]

    def request(path, body=None):
        req = urllib.request.Request(DEFAULT_PORTAL_URL + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with urlopen(req, timeout=60) as response:
            return json.load(response)

    inventory = request("/paper/api/deep-read/provenance-candidates")
    candidates = {item["paper_id"]: item for item in inventory["candidates"]}
    selected = list(candidates) if args.all else list(dict.fromkeys(args.paper_id))
    results = []
    for paper in selected:
        row = {"paper_id": paper, "status": "no_completed_analysis"}
        try:
            if paper not in candidates:
                results.append(row); continue
            query = urllib.parse.urlencode({"id": paper, "sections": "analysis"})
            analysis = request("/paper/api/papers/detail?" + query)["analysis"]
            source = analysis.get("provenance", {})
            if args.source_resident_id and source.get("resident_id") != args.source_resident_id:
                row["status"] = "source_mismatch"
            elif analysis.get("sha256"):
                if args.apply:
                    refreshed = request("/paper/api/deep-read/provenance", {"paper_id": paper,
                        "job_id": candidates[paper]["job_id"], "expected_analysis_sha256": analysis["sha256"],
                        "resolve_resident_model": True})
                    source = refreshed.get("usage", {}).get("generation_source", {})
                    row.update(model=refreshed.get("usage", {}).get("model"),
                               model_evidence=refreshed.get("usage", {}).get("model_evidence"))
                row.update(status="updated" if args.apply else "dry_run", source=source)
        except urllib.error.HTTPError as error:
            row.update(status="forbidden" if error.code == 403 else "unavailable" if error.code == 400 else "conflict" if error.code == 409 else "error",
                       http_status=error.code, error=error.read(2000).decode(errors="replace"))
        except (TimeoutError, OSError) as error:
            # A lost POST response does not prove the save failed. Never replay it.
            row.update(status="commit_unconfirmed" if args.apply else "read_error", error=str(error))
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if args.report:
            args.report.write_text(json.dumps({"inventory": inventory, "results": results}, ensure_ascii=False, indent=2))
    summary = {"visible_papers": inventory["visible_papers"], "without_completed_job": inventory["without_completed_job"],
               "selected": len(selected), "counts": dict(Counter(row["status"] for row in results))}
    if args.report:
        args.report.write_text(json.dumps({"inventory": inventory, "summary": summary, "results": results}, ensure_ascii=False, indent=2))
    print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

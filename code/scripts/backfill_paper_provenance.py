#!/usr/bin/env python3
"""Refresh managed Paper provenance through authenticated APIs, without AI calls.

python scripts/backfill_paper_provenance.py --resident-id <caller> --paper-id <paper>
Add --apply to save; --source-resident-id restricts the original generating Agent.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.resident_hatg_instruction import DEFAULT_PORTAL_URL, _resident_id, _token
from management_common.portal_agent_http import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resident-id", required=True)
    parser.add_argument("--paper-id", action="append", required=True)
    parser.add_argument("--source-resident-id")
    parser.add_argument("--apply", action="store_true")
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

    for paper in dict.fromkeys(args.paper_id):
        query = urllib.parse.urlencode({"id": paper, "sections": "analysis"})
        analysis = request("/paper/api/papers/detail?" + query)["analysis"]
        source = analysis.get("provenance", {})
        if args.source_resident_id and source.get("resident_id") != args.source_resident_id:
            print(json.dumps({"paper_id": paper, "status": "source_mismatch"})); continue
        job = request("/paper/api/deep-read/status?" + urllib.parse.urlencode({"paper_id": paper}))
        if job.get("status") != "done" or not analysis.get("sha256"):
            print(json.dumps({"paper_id": paper, "status": "no_completed_analysis"})); continue
        if args.apply:
            refreshed = request("/paper/api/deep-read/provenance", {"paper_id": paper,
                "job_id": job["job_id"], "expected_analysis_sha256": analysis["sha256"]})
            source = refreshed.get("usage", {}).get("generation_source", {})
        print(json.dumps({"paper_id": paper, "status": "updated" if args.apply else "dry_run", "source": source}, ensure_ascii=False))


if __name__ == "__main__":
    main()

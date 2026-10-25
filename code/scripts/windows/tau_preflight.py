"""Report native TAU readiness and core cases without starting a Campaign.

Exit 2 means prerequisites are missing, not that any TAU case has failed.
This probe never creates test identities, copies credentials or invokes a model.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from management_portal.test_framework.catalog import CatalogRepository
from management_portal.test_framework.runner_ids import TAU_RUNNER_ID
from management_portal.test_framework.tau import TauConfig, browser_runtime_issue, executor_runtime_issue


def report() -> dict:
    catalog = CatalogRepository(root=ROOT)
    _, catalog_errors = catalog.projection()
    selected = []
    for case in catalog.query({"features": ["core"], "lifecycle": ["active"]}):
        implementations = [item for item in case.implementations if item.runner == TAU_RUNNER_ID]
        if not implementations:
            continue
        selected.append({
            "id": case.id, "key": case.key, "title": case.title,
            "module": case.module, "importance": case.importance,
            "implementations": [{"id": item.id, "entrypoint": item.entrypoint,
                                 "source": item.source, "risk": item.risk}
                                for item in implementations],
        })
    sources = sorted({item["source"] for case in selected for item in case["implementations"]})
    config = TauConfig.from_environment()
    checks = {
        "executor": executor_runtime_issue(),
        "browser": browser_runtime_issue(),
        "configured_runner": config.unavailable_reason(),
    }
    blockers = [{"check": key, "reason": value} for key, value in checks.items() if value]
    if catalog_errors:
        blockers.append({"check": "catalog", "reason": "Catalog has invalid declarations; selection is incomplete."})
    if not selected:
        blockers.append({"check": "selection", "reason": "No active core TAU cases found."})
    return {
        "schema": "tfi.windows-tau-preflight.v1",
        "source_commit": os.environ.get("GITHUB_SHA", ""),
        "platform": {"system": platform.system(), "release": platform.release(),
                     "version": platform.version(), "python": platform.python_version()},
        "status": "blocked" if blockers else "ready",
        "campaign_status": "not_started",
        "executed_case_count": 0,
        "selection": {"features": ["core"], "lifecycle": ["active"], "runner": TAU_RUNNER_ID},
        "selected_case_count": len(selected),
        "by_module": dict(sorted(Counter(case["module"] for case in selected).items())),
        "by_importance": dict(sorted(Counter(case["importance"] for case in selected).items())),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        "blockers": blockers, "catalog_errors": catalog_errors, "cases": selected,
        "scope": "Readiness only. No live browser, Portal mutation, AI request or TAU verdict.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "platform", "status", "campaign_status", "selected_case_count", "executed_case_count", "blockers"
    )}, ensure_ascii=False))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

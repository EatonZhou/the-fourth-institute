#!/usr/bin/env python3
"""Generate one durable TFI release run into an isolated engineering directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("show", "generate", "report"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resolution", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument('--environment', type=Path, help='Explicit source instance environment file; values are never printed')
    parser.add_argument('--attempt', type=int, default=1, help='New isolated attempt of the same run; prior engineering is preserved')
    parser.add_argument('--data-manifest', type=Path, help='Reuse this run\'s immutable export for a code-only repair')
    parser.add_argument('--data-manifest-sha256', help='Required exact digest when reusing an immutable export')
    parser.add_argument('--upgrade-declaration', type=Path,
                        help='Explicit exact-origin upgrade contract JSON for code repair; migrations must be declared')
    args = parser.parse_args()
    if bool(args.data_manifest) != bool(args.data_manifest_sha256):
        parser.error('--data-manifest and --data-manifest-sha256 must be supplied together')
    if args.data_manifest and (args.action != 'generate' or args.resolution):
        parser.error('Data reuse is only for code repair generation without a new resolution')
    if args.upgrade_declaration and not args.data_manifest:
        parser.error('--upgrade-declaration requires code repair with --data-manifest')
    repair_options = {}
    if args.upgrade_declaration:
        repair_options['upgrade_declaration'] = json.loads(args.upgrade_declaration.read_text())
    if args.environment:
        from scripts.portal_setup import load_environment
        load_environment(str(args.environment))
    from management_portal.distribution.store import ReleaseStore, digest
    store = ReleaseStore()
    if args.action == "show":
        result = store.get_run(args.run_id)
        result["snapshot_sha256"] = digest(result["snapshot"])
    elif args.action == "generate":
        from management_portal.distribution.service import generate_run, rebuild_code_run
        if args.data_manifest:
            result = rebuild_code_run(args.run_id, data_manifest=args.data_manifest,
                                      expected_manifest_sha256=args.data_manifest_sha256, attempt=args.attempt,
                                      **repair_options)
        else:
            result = generate_run(args.run_id, resolution=json.loads(args.resolution.read_text()) if args.resolution else None, attempt=args.attempt)
    else:
        if not args.report:
            parser.error("report requires --report")
        result = store.report(args.run_id, **json.loads(args.report.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

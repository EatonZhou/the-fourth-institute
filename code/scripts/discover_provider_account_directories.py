#!/usr/bin/env python3
"""Discover host account directories offline, without opening credentials.

Uses the same bounded metadata-only engine as the management page. Runs as the
current OS user and never reads Portal registration data. Exclusions can be
supplied explicitly; the HTTP entry supplies its actual registered directories.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_portal.execution.account_directory_discovery import discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("codex", "claude", "antigravity", "kimi"))
    parser.add_argument("--excluded", action="append", default=[], help="排除的账号目录；可重复指定")
    args = parser.parse_args()
    result = discover(args.provider, excluded=args.excluded)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

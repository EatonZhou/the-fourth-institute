#!/usr/bin/env python3
"""Resolve a Portal area reference using an explicitly selected instance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common import host_instances
from management_common.instance_manifest import AREAS, load


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", nargs="?", help="area:relative/path or a canonical host path")
    parser.add_argument("--instance", help="Instance name in the OS user's registry; otherwise use PORTAL_INSTANCE_ROOT")
    args = parser.parse_args(argv)
    instance = host_instances.find(args.instance) if args.instance else load()
    if args.reference is not None:
        print(instance.resolve(args.reference))
    else:
        print(json.dumps({name: str(instance.area(name)) for name in AREAS}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

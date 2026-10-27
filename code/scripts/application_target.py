#!/usr/bin/env python3
"""Show or change which Resident or Lineage a Portal application gives its work to.

Applications: resident_discovery, resident_summary, notice_classifier,
semantic_search:<surface>, resource_chat:<resource_type> and tau. Every change
names the revision it was read at, so two maintainers cannot overwrite each
other. TAU must be paused before its target Lineage changes and is resumed
with a separate call; Campaigns already accepted keep running.

  application_target.py show tau
  application_target.py set tau --expected-revision 4 --pause --reason "move team" --actor operator
  application_target.py set tau --expected-revision 5 --target lineage_xxxxxxxxxxxx --actor operator
  application_target.py set tau --expected-revision 6 --resume --actor operator
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="print the current configuration")
    show.add_argument("application")
    change = commands.add_parser("set", help="change the target, admission pause or reason")
    change.add_argument("application")
    change.add_argument("--expected-revision", type=int, required=True, help="revision printed by show")
    change.add_argument("--actor", required=True, help="who makes the change; recorded as updated_by")
    change.add_argument("--target", help="new Resident or Lineage ID, by application type")
    pause = change.add_mutually_exclusive_group()
    pause.add_argument("--pause", action="store_true", help="refuse new work (tau only)")
    pause.add_argument("--resume", action="store_true", help="accept new work again (tau only)")
    change.add_argument("--reason")
    args = parser.parse_args(argv)

    from management_common.postgres_configuration import host_database_url
    from management_portal.society_service import application_target_configuration as configuration

    host_database_url()
    if args.command == "show":
        result = configuration.get(args.application)
    else:
        changes = {}
        if args.target is not None:
            changes["target_id"] = args.target
        if args.pause or args.resume:
            changes["admission_paused"] = bool(args.pause)
        if args.reason is not None:
            changes["reason"] = args.reason
        if not changes:
            parser.error("set needs --target, --pause, --resume or --reason")
        result = configuration.update(args.application, changes, args.actor, expected_revision=args.expected_revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Inspect and steer the Harness policy cells; run their daily evaluation.

The cells evolve from daily use (``management_portal.execution.harness_evolution``):
each Provider and load family has a champion parameter set and at most one
challenger running on a share of its sessions.  ``daily`` uses the same ExecutionService-owned routine as the
Lineage maintenance instruction: it keeps the calls of recent turns, decides every
challenger, starts new ones and writes the report; ``collect`` keeps the calls
only (hourly).  ``estimate`` replays a step on the recorded calls.  ``sample``
and ``label`` are the reviewer's side: it reads sampled turns and keeps its
judgement as their quality labels.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from management_portal.execution.harness_evolution import (  # noqa: E402
    DEFAULT_CHALLENGER_SHARE,
    HarnessEvolutionStore,
)
from management_portal.execution.harness_evolution_offline import (  # noqa: E402
    collect_turn_calls,
    estimate_step,
    load_turn_calls,
)
from management_portal.execution.harness_evolution_evaluation import (  # noqa: E402
    cell_traffic,
    record_review,
    sample_for_review,
)
from management_portal.execution.harness_policy_table import (  # noqa: E402
    TUNABLES,
    parse_override,
)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _actor(args: argparse.Namespace) -> str:
    actor = str(args.actor or "").strip() or f"operator:{getpass.getuser()}"
    if any(character.isspace() for character in actor):
        raise SystemExit("--actor takes one identity such as resident_<id> or a username; put notes in --reason")
    return actor


def daily(args: argparse.Namespace) -> dict[str, object]:
    from management_portal.execution.harness_evolution_runner import run_daily

    return run_daily(dry_run=args.dry_run, no_proposals=args.no_proposals,
                     auto_promote=args.auto_promote, report_path=args.report, status_path=args.status)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="", help="who makes a change (default operator:<user>)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="cells and their recent events")
    commands.add_parser("tunables", help="the parameters a challenger may change")

    traffic = commands.add_parser("traffic", help="counted turns per day of each cell, without tests")
    traffic.add_argument("--days", type=float, default=7.0)

    collect = commands.add_parser("collect", help="keep the model calls of recent turns for the replay")
    collect.add_argument("--days", type=float, default=3.0, help="look back this many days")

    estimate = commands.add_parser("estimate", help="replay a step from the champion on the recorded calls")
    estimate.add_argument("provider")
    estimate.add_argument("family")
    estimate.add_argument("--set", action="append", default=[], metavar="TUNABLE=VALUE")
    estimate.add_argument("--unset", action="append", default=[], metavar="TUNABLE")
    estimate.add_argument("--days", type=float, default=14.0)

    run = commands.add_parser("daily", help="evaluate, propose and write the report")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--no-proposals", action="store_true")
    run.add_argument("--auto-promote", action="store_true")
    run.add_argument("--report", default="", help="write the markdown report here")
    run.add_argument("--status", default="", help="write the status JSON here")

    sample = commands.add_parser("sample", help="turns for the reviewer to judge")
    sample.add_argument("--hours", type=float, default=24.0)
    sample.add_argument("--per-arm", type=int, default=4)
    sample.add_argument("--limit", type=int, default=24)

    label = commands.add_parser("label", help="keep the reviewer's judgement of one turn")
    label.add_argument("execution_id")
    label.add_argument("--result", choices=("pass", "fail"), required=True)
    label.add_argument("--reason", required=True)
    label.add_argument("--score", type=float)

    start = commands.add_parser("start", help="run a challenger in a cell")
    start.add_argument("provider")
    start.add_argument("family")
    start.add_argument("--set", action="append", default=[], metavar="TUNABLE=VALUE",
                       help="change from the champion; may be repeated")
    start.add_argument("--unset", action="append", default=[], metavar="TUNABLE",
                       help="go back to the Backend's value; may be repeated")
    start.add_argument("--share", type=float, default=DEFAULT_CHALLENGER_SHARE)
    start.add_argument("--reason", default="")

    for name, help_text in (
        ("promote", "make the challenger the champion"),
        ("withdraw", "end the challenger without a decision"),
        ("freeze", "stop all changes of a cell and end its challenger"),
        ("unfreeze", "let a frozen cell change again"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("provider")
        command.add_argument("family")
        command.add_argument("--reason", default="")

    args = parser.parse_args()
    store = HarnessEvolutionStore()

    if args.command == "status":
        _print({
            "cells": [cell.report() for cell in store.cells()],
            "events": store.events(since=time.time() - 30 * 86400, limit=50),
        })
    elif args.command == "tunables":
        _print({name: tunable.__dict__ for name, tunable in TUNABLES.items()})
    elif args.command == "traffic":
        _print({
            f"{provider}/{family}": daily
            for (provider, family), daily in sorted(
                cell_traffic(days=args.days).items(), key=lambda item: -item[1]
            )
        })
    elif args.command == "collect":
        _print(collect_turn_calls(lookback_days=args.days, store=store))
    elif args.command == "estimate":
        cell = store.cell(args.provider, args.family)
        champion = dict(cell.champion) if cell is not None else {}
        challenger = dict(champion)
        for item in args.set:
            name, value = parse_override(item)
            challenger[name] = value
        for name in args.unset:
            challenger.pop(name, None)
        now = time.time()
        _print(estimate_step(
            load_turn_calls(since=now - args.days * 86400.0, until=now, store=store),
            provider=args.provider,
            family=args.family,
            champion=champion,
            challenger=challenger,
        ))
    elif args.command == "daily":
        status = daily(args)
        if args.report or args.status:
            _print({key: status[key] for key in ("applied", "evaluations", "proposals")})
    elif args.command == "sample":
        now = time.time()
        _print(sample_for_review(
            since=now - args.hours * 3600.0,
            until=now,
            per_arm=args.per_arm,
            limit=args.limit,
        ))
    elif args.command == "label":
        record_review(args.execution_id, result=args.result, reason=args.reason, score=args.score)
        _print({"execution_id": args.execution_id, "result": args.result})
    elif args.command == "start":
        cell = store.cell(args.provider, args.family)
        overrides = dict(cell.champion) if cell is not None else {}
        for item in args.set:
            name, value = parse_override(item)
            overrides[name] = value
        for name in args.unset:
            overrides.pop(name, None)
        updated = store.start_challenger(
            args.provider, args.family, overrides,
            actor=_actor(args), share=args.share,
            evidence={"reason": args.reason or "operator"},
        )
        _print(updated.report())
    else:
        evidence = {"reason": args.reason or args.command}
        if args.command == "promote":
            updated = store.promote(args.provider, args.family, actor=_actor(args), evidence=evidence)
        elif args.command == "withdraw":
            updated = store.retire(args.provider, args.family, "withdrawn", actor=_actor(args), evidence=evidence)
        else:
            updated = store.freeze(
                args.provider, args.family, actor=_actor(args),
                frozen=args.command == "freeze",
            )
        _print(updated.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

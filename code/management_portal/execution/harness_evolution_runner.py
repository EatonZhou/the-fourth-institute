"""Execution-owned daily evolution; shared by the ISA job and the operator CLI."""
from __future__ import annotations

import time
from pathlib import Path

from .harness_evolution import HarnessEvolutionStore
from .harness_evolution_offline import collect_turn_calls, load_turn_calls
from .harness_evolution_evaluation import (
    EvolutionCriteria, cell_turns, evaluate_cells, propose_challengers, render_report, traffic_of,
)
from management_portal.shared_state import atomic_replace_json


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def run_daily(*, dry_run: bool = False, no_proposals: bool = False, auto_promote: bool = False,
              report_path: str = "", status_path: str = "") -> dict[str, object]:
    store = HarnessEvolutionStore()
    criteria = EvolutionCriteria(auto_promote=bool(auto_promote))
    now = time.time()
    apply = not dry_run
    # Keeping calls is no decision: a dry run keeps them too.
    collected = collect_turn_calls(now=now, store=store)
    calls = load_turn_calls(since=now - criteria.offline_days * 86400.0, until=now, store=store)
    evaluations = evaluate_cells(store, now=now, criteria=criteria, apply=apply, calls=calls)
    turns = cell_turns(now=now, days=criteria.traffic_days)
    traffic = traffic_of(turns, criteria.traffic_days)
    proposals = (
        propose_challengers(
            store, now=now, criteria=criteria, apply=apply,
            traffic=traffic, turns=turns, calls=calls,
        )
        if not no_proposals
        else []
    )
    cells = store.cells()
    report = render_report(
        cells=cells,
        evaluations=evaluations,
        proposals=proposals,
        traffic=traffic,
        generated_at=now,
        collected=collected,
    )
    status = {
        "generated_at": now,
        "applied": apply,
        "collected": collected,
        "evaluations": [
            {key: item[key] for key in ("cell", "decision", "reasons", "applied", "version")}
            for item in evaluations
        ],
        "proposals": proposals,
        "cells": [cell.report() for cell in cells],
        "traffic": {f"{provider}/{family}": daily for (provider, family), daily in sorted(traffic.items())},
    }
    if report_path:
        _write_text(Path(report_path), report)
    if status_path:
        path = Path(status_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace_json(path, status, sort_keys=False)
    if not report_path:
        print(report)
    return status

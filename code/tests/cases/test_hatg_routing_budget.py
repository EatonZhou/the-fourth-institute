"""HATG routing geometry and bounded card reads.

:module: lineage.hatg
"""
import subprocess
from pathlib import Path


def test_routing_card_reads_and_drag_invalidation():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "hatg_routing_budget.cjs")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_program_lookup_reuse_and_invalidation():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "hatg_program_lookup_budget.cjs")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

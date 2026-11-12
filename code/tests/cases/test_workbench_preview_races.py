"""Studio preview replacement and cancellation before host mounting.

:module: workbench.panels
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("boundary", ["studio", "host", "region"])
def test_workbench_preview_races(boundary):
    script = Path(__file__).with_name("javascript") / "workbench_preview_races.cjs"
    result = subprocess.run(
        ["node", "--test", f"--test-name-pattern=^{boundary} ", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

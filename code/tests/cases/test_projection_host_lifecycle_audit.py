"""Projection bootstrap retry, confirmation ownership and SDK lifetime.

:module: workbench.panels
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("scenario", ["bootstrap", "confirmation", "sdk", "application"])
def test_projection_host_lifecycle_audit(scenario):
    script = Path(__file__).with_name("javascript") / "projection_host_lifecycle_audit.cjs"
    result = subprocess.run(
        ["node", "--test", f"--test-name-pattern=^{scenario} ", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

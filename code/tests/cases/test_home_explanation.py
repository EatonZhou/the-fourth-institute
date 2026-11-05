"""Composite explanation page: fixed content, local demo isolation and media lifetime.

:module: workbench.preview
:importance: P1
"""
from pathlib import Path
import subprocess


def test_home_explanation_browser():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "home_explanation.browser.cjs")],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

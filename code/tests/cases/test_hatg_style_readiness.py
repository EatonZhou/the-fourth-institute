"""Native HATG stylesheet and template recovery.

:module: lineage.hatg
"""
import subprocess
from pathlib import Path


def test_hatg_style_readiness():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "hatg_style_readiness.browser.cjs")],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Family facade identity in native project HATG references.

:module: lineage.hatg
"""
import subprocess
from pathlib import Path


def test_hierarchy_reference_family_contract():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "hatg_reference_family.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_hierarchy_reference_family_navigation():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "hatg_reference_family.browser.cjs")],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

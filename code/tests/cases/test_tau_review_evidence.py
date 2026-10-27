"""Review evidence must finish in its original action window.

:module: resident.conversation
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_review_original_window_requires_exact_visible_final():
    """原生 Review 原动作核对捕获 Diff、原输入和可见终态，接纳不等于完成，关闭后不可补写"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_review_evidence.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

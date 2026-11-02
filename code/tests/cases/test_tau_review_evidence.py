"""Review observations remain useful when some report material is absent.

:module: resident.conversation
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_review_collects_partial_observations_and_supplements():
    """原生 Review 保留实际观察，缺材料可补记，不产生使用结果判定。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_review_evidence.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

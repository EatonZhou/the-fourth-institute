"""Finite body observation and closure for native workbench HTTP evidence.

:module: workbench.panels
:importance: P1
"""
from pathlib import Path
import subprocess


def test_editor_original_response_body_observation_is_bounded():
    """原生响应正文停滞时保留HTTP状态、有限收尾且拒绝迟到补采"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_editor_response_deadline.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

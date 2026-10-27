"""Original editor responses and native navigation, without replay.

:module: workflow.studio
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("script", ["tau_workbench_editor_evidence.cjs", "tau_native_navigation_evidence.cjs"])
def test_editor_evidence_uses_original_identity_and_passive_reads(script):
    """编辑器发布与原生导航取证核对原对象、响应及可见状态，不重放请求或用后验补写原证据"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / script)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Workbench active Open target persistence.

:module: workbench.panels
:design: decision_538e155e1102
"""
from pathlib import Path
import subprocess


def test_active_target_survives_reload_after_focus():
    """已激活标签和面板正文点击均保存 Open 目标，刷新保留目标。"""
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("javascript") / "workbench_active_target.cjs")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

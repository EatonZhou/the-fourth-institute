"""Runtime advanced-entry readiness belongs to the dialog opened by the controller.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_runtime_advanced_entry():
    """高级入口核实际运行设置表单，不在旧Menu中等待textbox"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_runtime_advanced_entry.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

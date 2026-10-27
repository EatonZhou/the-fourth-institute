"""Prompt injection entry selects its preset control, separately from preview.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_prompt_injection_entry():
    """原生Prompt编辑器按预设控件就绪，分类和预览控件不混为目标"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_prompt_injection_entry.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

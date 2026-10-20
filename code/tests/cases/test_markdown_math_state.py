"""Markdown formula safety through the production renderer and repository KaTeX.

:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_inline_and_fenced_formulas_preserve_code_and_reject_unsafe_commands():
    """行内和块级公式经 KaTeX 渲染，代码保持原文，不安全命令不生成可执行标记

    Stable case test_a05c80e33fce; Monaco/fonts remain browser coverage."""
    script = Path(__file__).with_name("javascript") / "markdown_math_state.cjs"
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "contract passed: inline_fenced_math"

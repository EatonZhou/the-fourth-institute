"""Markdown Mermaid import gating; actual SVG rendering remains browser coverage.

:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_only_complete_mermaid_fences_request_the_diagram_engine():
    """只有闭合完整的 mermaid 代码块才加载图表引擎，且只请求一次

    Stable case test_8bb9fd65f549; imported renderer remains unresolved."""
    script = Path(__file__).with_name("javascript") / "markdown_mermaid_loading.cjs"
    result = subprocess.run(
        ["node", "--experimental-vm-modules", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "contract passed: complete_mermaid_loading"

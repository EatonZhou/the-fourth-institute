"""Explicit MCP tool selection in the Resident capability form.

:module: workbench.chat
:importance: P1
"""
from pathlib import Path
import subprocess


def test_resident_mcp_tool_selection():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("javascript") / "resident_mcp_selection.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Quoted forwards retain Markdown blocks and separate the forwarding note.

:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_forwarded_markdown_blocks_keep_source_boundaries_and_safety():
    """转发引用保留 Markdown 块、来源边界与安全限制"""
    script = Path(__file__).with_name("javascript") / "markdown_blockquote.cjs"
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "contract passed: forwarded_markdown_blocks"

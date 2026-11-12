"""Knowledge paging replaces content without collapsing the document.

:module: workflow.knowledge
:importance: P1
"""
from pathlib import Path
import subprocess


def test_knowledge_paged_content_preserves_scroll():
    script = Path(__file__).with_name("javascript") / "knowledge_board_content_transition.browser.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

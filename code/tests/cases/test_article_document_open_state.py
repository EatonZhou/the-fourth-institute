"""Background Article file reads leave the active editor's status intact."""
from pathlib import Path
import subprocess


def test_article_document_open_state():
    script = Path(__file__).with_name("javascript") / "article_document_open_state.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr

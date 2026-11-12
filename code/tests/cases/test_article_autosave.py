"""Article idle saves preserve in-flight edits and never request a build."""
from pathlib import Path
import subprocess


def test_article_autosave():
    source = Path(__file__).with_name("javascript") / "article_autosave.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

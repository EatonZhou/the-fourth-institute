"""Article context shortcut uses the same selection action as the toolbar."""
from pathlib import Path
import subprocess


def test_article_context_shortcut_in_browser():
    script = Path(__file__).with_name("javascript") / "article_context_shortcut.browser.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

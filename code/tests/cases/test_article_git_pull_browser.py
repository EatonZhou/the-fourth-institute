"""Article Git controls and credentials in Chromium, using the shared browser slot."""
from pathlib import Path
import subprocess

from management_portal.test_framework.slots import acquire


def test_article_git_pull_browser():
    script = Path(__file__).with_name("javascript") / "article_git_pull.browser.cjs"
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

"""Article external updates in the real editor, with shared browser admission."""
from pathlib import Path
import subprocess

from management_portal.test_framework.slots import acquire


def test_article_content_sync_browser():
    script = Path(__file__).with_name("javascript") / "article_content_sync.browser.cjs"
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

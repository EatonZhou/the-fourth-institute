"""Exercise both Article editing surfaces with real save and compile buttons."""
from pathlib import Path
import subprocess
from management_portal.test_framework.slots import acquire


def test_article_autosave_browser():
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        script = Path(__file__).with_name("javascript") / "article_autosave.browser.cjs"
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

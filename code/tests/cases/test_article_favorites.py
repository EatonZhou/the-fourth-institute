"""Article navigation uses the shared favorites owner and real browser controls."""
from pathlib import Path
import subprocess

from management_portal.test_framework.slots import acquire


def test_article_favorites_browser():
    lease = acquire('browser', 1, stop=lambda: False)
    try:
        script = Path(__file__).with_name('javascript') / 'article_favorites.browser.cjs'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

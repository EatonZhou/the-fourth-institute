from pathlib import Path
import subprocess
from management_portal.test_framework.slots import acquire


def test_article_comments_browser():
    lease = acquire('browser', 1, stop=lambda: False)
    try:
        result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'article_comments.browser.cjs')], capture_output=True, text=True, timeout=75)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

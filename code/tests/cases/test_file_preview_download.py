"""File preview toolbar preserves the source's original-file download contract."""
from pathlib import Path
import subprocess


def test_file_preview_toolbar_downloads_current_original():
    """Cover the real host wiring, file changes and access loss.

    :design: decision_61e50306cc79
    """
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).with_name('javascript') / 'file_preview_download.browser.cjs'
    subprocess.run(['node', str(script)], cwd=root, check=True, timeout=90)

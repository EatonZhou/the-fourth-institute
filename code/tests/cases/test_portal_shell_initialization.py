"""Theme controls can mount after DOM ready using the production script order."""
from pathlib import Path
import subprocess


def test_shell_theme_initializes_after_dom_ready():
    source = Path(__file__).with_name('javascript') / 'portal_shell_initialization.cjs'
    result = subprocess.run(['node', str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

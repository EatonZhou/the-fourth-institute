"""Canvas zoom and split action toolbar retain styles when portalled.

:module: lineage.hatg
"""
from pathlib import Path
import subprocess


def test_canvas_split_toolbar_and_portalled_menu():
    script = Path(__file__).with_name('javascript') / 'hatg_toolbar_popover.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

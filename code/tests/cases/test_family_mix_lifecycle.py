"""Family preference read ownership follows mounted Chat controls.

:module: workbench.chat
:importance: P2
"""
from pathlib import Path
import subprocess


def test_family_preferences_stop_post_save_reads_after_last_view_destroyed():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'family_mix_lifecycle.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

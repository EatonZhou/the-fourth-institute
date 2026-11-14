"""Home manual refresh dispatches independent visible owners together.

:module: interface.shell
:importance: P2
"""
from pathlib import Path
import subprocess


def test_home_refresh_does_not_wait_for_accounts_before_other_visible_owners():
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'home_refresh_fanout.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

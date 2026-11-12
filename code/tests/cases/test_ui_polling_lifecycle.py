"""Management instances and Mailbox Todo background read lifetimes.

:module: workbench.shell
:importance: P1
"""
from pathlib import Path
import subprocess


def test_instance_and_todo_polling_stops_when_hidden_denied_or_destroyed():
    source = Path(__file__).with_name('javascript') / 'ui_polling_lifecycle.cjs'
    result = subprocess.run(['node', str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

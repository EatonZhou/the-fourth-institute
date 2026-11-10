"""Persisted Terminal panels reconnect through the session creation owner.

:module: resident.workspace-memory
:importance: P1
"""
from pathlib import Path
import subprocess

from management_portal import local_terminal_backend as terminal


def test_terminal_restored_session_lifecycle():
    script = Path(__file__).with_name('javascript') / 'terminal_restore.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_restored_panel_browser():
    script = Path(__file__).with_name('javascript') / 'terminal_restore.browser.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_start_reuses_live_shell_and_replaces_missing_session(tmp_path):
    actor = 'terminal-reconnect-fixture'
    first = terminal.start_terminal(actor, cwd=tmp_path)
    ids = {first['session_id']}
    try:
        resumed = terminal.start_terminal(actor, session_id=first['session_id'], cwd=tmp_path)
        assert resumed['session_id'] == first['session_id']
        assert resumed['pid'] == first['pid']
        terminal.control_terminal(actor, first['session_id'], 'close')
        ids.remove(first['session_id'])
        replacement = terminal.start_terminal(actor, session_id=first['session_id'], cwd=tmp_path)
        ids.add(replacement['session_id'])
        assert replacement['alive']
        assert replacement['session_id'] != first['session_id']
        assert replacement['cwd'] == str(tmp_path)
    finally:
        for session_id in ids:
            terminal.control_terminal(actor, session_id, 'close')

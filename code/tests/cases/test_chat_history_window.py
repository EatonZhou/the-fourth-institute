"""Expanded Chat history survives authoritative snapshot recovery.

:module: workbench.chat
:importance: P1
"""
import json
import os
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_history_window_refresh():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_history_window.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_history_window_initial_identity():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_history_window.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        env={**os.environ, 'HISTORY_WINDOW_IDENTITY_ONLY': '1'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_chat_switch_resume():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_history_window.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        env={**os.environ, 'CHAT_RESUME_ONLY': '1'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

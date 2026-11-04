"""Personal Chat marker choices share storage, accessibility and state semantics.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_round_marker_choices():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_round_marker_choices.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

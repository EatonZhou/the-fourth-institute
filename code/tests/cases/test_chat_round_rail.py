"""Shared Chat round rail: measured reading, personal flags and request status.

:module: workbench.chat
:importance: P1
"""
import json
import os
from pathlib import Path
import subprocess
import pytest

from management_portal.resources.portal import portal_route_script


@pytest.mark.parametrize('mode', ['', 'RAIL_REVIEW_ONLY', 'RAIL_PROTOCOL_ONLY', 'RAIL_VISUAL_ONLY', 'RAIL_MARKERS_ONLY', 'RAIL_PREVIEW_ONLY', 'RAIL_REFERENCES_ONLY', 'RAIL_HIT_ONLY', 'RAIL_HISTORY_ONLY'])
def test_round_rail_real_component(mode):
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_round_rail.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        env={**os.environ, **({mode: '1'} if mode else {})},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

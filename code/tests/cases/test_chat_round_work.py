"""Per-request execution work reaches the shared Chat rail through its native protocol.

:module: workbench.chat
:importance: P1
"""
import json
import os
from pathlib import Path
import subprocess
import pytest

from management_portal.resources.portal import portal_route_script


@pytest.mark.parametrize('mode', ['', 'RAIL_BOTTOM_ONLY', 'RAIL_QUEUE_ONLY'])
def test_round_work_contract(mode):
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_round_work.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        env={**os.environ, **({mode: '1'} if mode else {})},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

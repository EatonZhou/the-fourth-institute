"""Shared controls retain their authored skin inside page hosts.

:module: workbench.chat
:importance: P1
"""
import json
import os
from pathlib import Path
import subprocess

import pytest
from management_portal.resources.portal import portal_route_script


def test_control_skins_survive_host_defaults(tmp_path):
    code = Path(__file__).resolve().parents[2]
    if not (code / 'management_portal/test_framework/tau_runtime/node_modules/playwright').is_dir():
        pytest.skip('requires Portal browser test runtime')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'control_style_ownership.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        env={**os.environ, 'CONTROL_STYLE_EVIDENCE': str(tmp_path)},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

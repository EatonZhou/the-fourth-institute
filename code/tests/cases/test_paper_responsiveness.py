"""Paper startup and idle Reader layout, without user data or model calls.

:module: web.workflows
:design: decision_528bf9985705
:importance: P1
"""
from pathlib import Path
import os
import subprocess

import pytest
from management_portal.resources.portal import portal_route_script


@pytest.mark.parametrize('script', ['paper_reader_idle_layout.cjs', 'paper_startup_responsiveness.cjs'])
def test_paper_responsiveness(script, tmp_path):
    routes = tmp_path / 'portal_resource_routes.js'
    routes.write_bytes(portal_route_script())
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / script)],
                            capture_output=True, text=True, timeout=60,
                            env={**os.environ, 'PORTAL_TEST_RESOURCE_ROUTES': str(routes)})
    assert result.returncode == 0, result.stdout + result.stderr

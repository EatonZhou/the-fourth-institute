"""Resident message heads navigate consistently in every Chat host."""
import json
import os
import subprocess
from pathlib import Path

from management_portal.resources.portal import portal_route_script


def test_resident_sender_and_current_resident_headers_navigate():
    source = Path(__file__).with_name('javascript') / 'chat_round_rail.browser.cjs'
    result = subprocess.run(['node', str(source)], text=True, capture_output=True,
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        env={**os.environ, 'CHAT_AUTHOR_LINKS_ONLY': '1'}, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

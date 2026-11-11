import json
from pathlib import Path
import subprocess
from management_portal.resources.portal import portal_route_script


def test_shared_chat_business_history():
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'chat_business_history.cjs')],
        input=json.dumps({'routeScript':portal_route_script().decode()}), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

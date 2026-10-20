"""Family cancellation remains visible when an already-delivered request is retained."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_retained_cancelled_family_request_has_visible_status():
    script = Path(__file__).with_name("javascript") / "family_cancelled_request.cjs"
    result = subprocess.run(
        ["node", str(script)], input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

"""Workspace image references use the shared authenticated image viewer."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script
from management_portal.test_framework.slots import acquire


def test_chat_local_image_reference_opens_centered_viewer():
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("javascript") / "chat_local_image_preview.browser.cjs")],
            input=json.dumps({"routeScript": portal_route_script().decode()}),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

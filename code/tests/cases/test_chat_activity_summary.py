"""Live activity headings and expanded history share the latest event.

:module: workbench.chat
:aspects: ui, behavior
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_live_activity_summary_follows_latest_event():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_activity_summary.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "PASS latest activity summary\n"

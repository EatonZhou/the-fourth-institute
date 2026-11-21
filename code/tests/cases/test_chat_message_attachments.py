"""Sent-message attachments share one identity across body and media metadata.

:aspects: ui, behavior
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_sent_attachment_identity_and_layout():
    source = Path(__file__).with_name("javascript") / "chat_message_attachments.browser.cjs"
    result = subprocess.run(
        ["node", str(source)], input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

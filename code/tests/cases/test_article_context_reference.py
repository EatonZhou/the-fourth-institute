"""Article selection reaches the real Chat composer through its keyboard command."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_article_context_reference_in_browser():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "article_context_reference.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

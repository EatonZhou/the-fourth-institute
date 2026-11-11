"""Article Chat can be configured before its first message without losing drafts."""
from pathlib import Path
import json
import subprocess
from management_portal.resources.portal import portal_route_script


def test_article_chat_settings_lifecycle_in_browser():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "article_chat_settings.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

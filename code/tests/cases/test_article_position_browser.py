"""Article reading positions survive navigation without caching source content."""
from pathlib import Path
import json
import subprocess
from management_portal.test_framework.slots import acquire
from management_portal.resources.portal import portal_route_script
from management_portal.page_bootstrap import inject_page_bootstrap
from management_portal.script_groups import expand_document


def test_article_position_browser():
    lease = acquire("browser", 1, stop=lambda: False)
    try:
        script = Path(__file__).with_name("javascript") / "article_position.browser.cjs"
        document = Path(__file__).parents[2] / "management_portal/article_workspace.html"
        fixture = {"routes": portal_route_script().decode(),
                   "html": inject_page_bootstrap(expand_document(document.read_text()).encode()).decode()}
        result = subprocess.run(["node", str(script)], input=json.dumps(fixture), capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

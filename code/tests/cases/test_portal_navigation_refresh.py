"""Native navigation remains usable while its readable catalog refreshes."""
from pathlib import Path
import os
import subprocess
import pytest

from management_portal.resources.portal import portal_route_script


@pytest.mark.parametrize("mode", ["interaction", "ordering", "secondary-pointer"])
def test_navigation_hydration_preserves_click_and_keyboard_target(mode, tmp_path):
    """导航目录异步返回时保留原生鼠标点击与键盘焦点。

    :module: workbench.panels
    :design: decision_e486671af066
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "portal_nav_refresh.cjs"
    routes = tmp_path / "portal_resource_routes.js"
    routes.write_bytes(portal_route_script())
    result = subprocess.run(["node", str(script), mode], capture_output=True, text=True, timeout=60,
                            env={**os.environ, "PORTAL_TEST_RESOURCE_ROUTES": str(routes)})
    assert result.returncode == 0, result.stdout + result.stderr

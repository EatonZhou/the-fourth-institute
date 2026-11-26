"""Resident Menu 首次打开时加载完整的模块集合。

:module: workbench.panels
:design: decision_08bb2d9141f9
:importance: P1
"""
import os
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_resident_menu_opens_without_preloaded_parts(tmp_path):
    """通过真实 PortalNav 首次打开菜单，创建真实外壳及 Memory 等部件，不访问业务数据。"""
    routes = tmp_path / 'portal_resource_routes.js'
    routes.write_bytes(portal_route_script())
    script = Path(__file__).with_name('javascript') / 'resident_menu_lazy_loading.browser.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60,
                            env={**os.environ, 'PORTAL_TEST_RESOURCE_ROUTES': str(routes)})
    assert result.returncode == 0, result.stdout + result.stderr

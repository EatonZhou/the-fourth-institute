"""Article links open the editing workspace; nested files remain in Preview.

:module: workbench.chat
:importance: P1
"""
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_article_links_open_editor_in_browser():
    """裸 ID、页面链接和资源 Chip 进入编辑页；文件预览与无权限反馈保持可用。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "article_resource_links.browser.cjs")],
        input=portal_route_script().decode(), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""The chat workspace loads the image viewer on demand through loadAsset.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_image_viewer_loads_once_reports_failure_and_retries():
    """图片查看器按需经 loadAsset 加载一次，失败时提示并可重试

    对话工作区经 PortalUIPrimitives.loadAsset 加载图片查看器的样式和脚本，各一个标签并带页面的资源版本；等待中的多次预览共用一次加载；加载失败时提示“图片查看器加载失败，请重试”并移除标签，下次预览重新加载；脚本运行后打开图片，之后的预览不再加载。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_image_editor_load.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

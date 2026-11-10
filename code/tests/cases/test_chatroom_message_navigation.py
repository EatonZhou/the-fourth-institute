"""Chatroom message links use the real viewport without reading unseen messages."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_chatroom_message_links_in_browser():
    """精确消息链接跨页定位、确认展示范围；旧提及不抢跳，失败不已读，撤回仅显示占位。

    :module: resident.conversation
    :importance: P1
    :design: decision_a6bebf4ac283
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chatroom_message_navigation.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

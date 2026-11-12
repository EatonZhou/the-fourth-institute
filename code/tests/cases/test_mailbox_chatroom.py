"""Mailbox embeds the shared Chatroom with isolated browser data."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_mailbox_chatroom_in_browser():
    """群聊内嵌、发送、返回位置与生命周期；无权及账号切换不泄漏内容。

    :module: resident.conversation
    :importance: P1
    :design: decision_15f7df737ed5
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "mailbox_chatroom.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

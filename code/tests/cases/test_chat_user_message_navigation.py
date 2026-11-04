"""Navigate user messages from the composer without changing the draft.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_ctrl_arrows_navigate_user_messages_in_browser():
    """Ctrl+方向键定位当前聊天的人类发言，保留草稿和焦点，普通方向键仍恢复输入。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_user_message_navigation.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

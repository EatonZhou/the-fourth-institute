"""Navigate user messages from the composer without changing the draft.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_alt_arrows_navigate_user_messages_in_browser():
    """Alt/Option+方向键定位人类发言；Ctrl不再跳转；草稿和普通输入历史保持不变。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_user_message_navigation.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

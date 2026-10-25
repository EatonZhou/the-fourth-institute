"""The composer leaves its keys to the input method while it composes.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_composer_waits_for_composition_before_send_and_history():
    """输入法组字期间，Ctrl/⌘ + Enter、发送按钮和上下箭头历史都不动作

    属于输入法的按键（isComposing 或 keyCode 229）不发送；compositionstart 到 compositionend 之间，Ctrl+Enter 和发送按钮都不发送，上箭头不取历史；组字结束后 Ctrl+Enter 只发送一次，上箭头取回历史输入。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_composer_ime.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

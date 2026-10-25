"""The composer's Refs popover picks the Skills and Agents a message carries.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_refs_popover_loads_searches_sends_and_clears_the_selection():
    """Refs 弹层首次打开时读取目录、失败可重试，搜索、计数、随消息发送并可清空

    Refs 弹层首次打开才读取 Skills 和 Agents 目录，读取失败时显示提示和重试按钮，读到后不再重复读取；搜索匹配名称、ID、路径和描述；勾选的项显示在 Refs 按钮的计数和摘要里，随下一条消息发送；Clear 清空选择，Done 关闭弹层，重新打开保留勾选。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_refs_control.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

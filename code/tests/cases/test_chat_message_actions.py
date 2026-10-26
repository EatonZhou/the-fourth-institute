"""A Chat message offers its actions: its menu, copy, links, host actions and folds.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_message_offers_its_actions():
    """右键消息打开它的菜单，消息下方的空隙归这条消息；菜单提供复制、打开链接和宿主动作

    右键消息时菜单在指针处打开，挪到页面层，另一条消息的菜单打开时前一个关闭，Escape 和点击外部都会关闭；文本框保留浏览器菜单，空白处不给菜单并关闭已打开的菜单；消息到下一条消息之间的空隙归它，下一行不是消息时空隙不归它，两侧留白、运行卡片前的空隙和对话之后的空白也不归任何消息；指针下是网页链接时菜单多出“打开链接”，在新标签页打开且不带来源；复制单条消息的纯文本或 Markdown，第一条回复还能复制整组回复，宿主可以提供复制内容；菜单内和消息里的动作交给宿主，渲染选项里的处理函数优先，失败时报告错误；交接卡片的“替我问一下”另发一条消息，发送期间按钮停用，对话不能发送时不发；连续回复组可以折叠和展开，默认折叠的组也可以展开；折叠 Family 工作后焦点留在它的按钮上。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_actions.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

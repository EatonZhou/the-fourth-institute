"""The message list's viewport: reader scrolls, following the tail, the reading place across Workbench layouts.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_message_viewport_follows_reader_scrolls_and_keeps_the_place_across_layouts():
    """只有读者的滚动改变跟随底部模式，工作台布局前后保住阅读位置，销毁时取消尚未执行的恢复

    滚轮、滚动键、拖动滚动条和触摸各算一次读者滚动，程序滚动和其他按键不算；离开底部进入阅读历史，回到底部重新跟随。打开另一个对话时忘掉进行中的手势。工作台布局前记住视口里第一条顶边可见的消息和它的偏移，布局后的两帧把它放回原处，期间暂停浏览器的滚动锚定；同一次布局的多次通知只记第一次，打开再关闭面板回到同一位置，重新渲染后按消息找回新行，比视口还高的消息保持滚过的比例，读者中途滚动则放弃恢复。跟随底部时布局和窗口缩放后仍停在底部。销毁工作区时恢复滚动锚定、取消排好的帧并停止监听。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_viewport.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""A Chat loads earlier messages, opens its Fork card, goes to quoted messages and keeps one quote per conversation.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_message_history_and_quotes():
    """加载更早消息、Fork 卡片的压缩摘要、引用跳转和输入框的引用条，销毁后不再响应

    “加载更早消息”只向宿主请求一次，加载期间按钮不可用并显示正在加载，完成或失败后恢复，失败交给宿主的 onError，没有时提示；没有更早消息时不显示按钮；Fork 卡片第一次展开时读取开头的压缩，读取期间可以收起，再展开不重复读取，读到的摘要保留，重绘列表后保持展开；读不到压缩或读取失败时显示原因，重新展开再读一次；另一个对话有自己的 Fork 卡片，迟到的读取结果不画进别的对话，也不夺走当前卡片的焦点；引用条显示当前对话的引用，设置引用后焦点留在输入框，引用需要消息和文字，切换对话显示该对话自己的引用，取消按钮移除引用并回到输入框，按消息清除只清除仍引用该消息的引用；点击消息里的引用块会跳到被引用的消息，不在列表里时加载更早的消息直到找到，已在列表里不再加载，没有消息 ID 时不查找也不提示，没有更早消息时不加载，已不在对话中时提示，加载失败交给宿主或提示；宿主在下一帧才画出更早的消息时等它画出，一次加载没有带来更早的消息就停止并提示，加载途中切换到别的对话时安静地停止，查找途中销毁对话后停止查找；销毁后点击加载更早、Fork 卡片、引用块和取消引用都不再向宿主请求或改变引用。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_history.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

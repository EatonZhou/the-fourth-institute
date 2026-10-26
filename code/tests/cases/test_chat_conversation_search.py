"""Searching a conversation from the Chat header.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_conversation_search_lists_matches_pages_and_moves_to_results():
    """会话内搜索列出自己的消息、匹配查询、分页读取服务端结果并跳转到所选消息

    没有查询时按时间倒序列出读者自己发出的非空消息，不含其他 Resident 送来的消息；查询不分大小写匹配已加载的消息并标出命中，长消息的摘录从命中附近开始。可以加载更早的历史；输入停顿后才发送服务端查询，下一页沿用分页令牌，加载期间不重复请求，已列出的结果不重复，旧查询迟到的结果和下一页被丢弃，下一页失败后重试从第一页重新开始。点击结果或在输入框按 Enter 关闭搜索并跳转到消息，方向键在结果间移动；不在当前对话中的结果给出提示。切换到其他对话时关闭搜索并清空查询，销毁 Chat 时移除搜索面板。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_conversation_search.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

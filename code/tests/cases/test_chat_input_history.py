"""The composer's Arrow history reads what the person typed from the server (sample_admin 10-16).

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_arrow_history_lists_queued_and_sent_inputs_then_the_server_pages():
    """上下箭头历史先列排队和刚发出的输入，再列服务端分页

    输入框按上箭头先取登录者排队中的输入和刚发出、服务端尚未列出的输入，再按需读服务端分页；其他成员的排队消息和输入不进入历史；服务端列出刚发出的输入后只算一条；Chat 出现登录者的新输入时重新读第一页，回复或他人消息不触发读取；每个会话的历史分开，读取失败时报告错误，之后按上箭头重新读取。队列删除后当前页面仍可恢复原文和附件，迟到的空历史页不会清掉它；向下键恢复原草稿，切换会话不串入旧输入。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_input_history.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

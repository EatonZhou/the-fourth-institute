"""The Chatrooms side view of the lineage page through production JavaScript."""
from pathlib import Path
import json
import subprocess


def test_lineage_workbench_chatrooms():
    """聊天室精确消息链接与站内导航、引用提及、列表置顶和活动排序。"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_chatrooms.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_chatroom_attachment_quotes_in_browser():
    """附件可引用；删除建议的用户或 Resident 提及后，刷新和发送不补回，原消息仍可跳转。

    :importance: P1
    :design: decision_a6bebf4ac283
    """
    from management_portal.resources.portal import portal_route_script

    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chatroom_attachment_quotes.browser.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

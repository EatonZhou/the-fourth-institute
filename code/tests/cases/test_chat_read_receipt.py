"""The read receipt of a Resident's conversation.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_read_receipt_waits_for_a_seen_conversation_and_retries():
    """读者看得到对话时才发已读回执，同一呈现只发一次，失败后等页面回来或五秒后重试

    对话呈现后在下一帧发出已读回执；同一呈现不重复发送，新的未读呈现、收件箱重新标为未读或更新的已读版本会再次发送。页面隐藏、Chat 未呈现、正在加载或对话视图保留回执时不发送，页面重新可见后补发。一次只发一条回执，期间到来的呈现在它结束后补发。失败或被拒绝后暂停重试，页面获得焦点时立即重试，否则五秒后重试；销毁后不再发送，也不再监听会话存储。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_read_receipt.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

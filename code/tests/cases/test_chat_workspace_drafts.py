"""Each conversation keeps its own composer draft.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_each_conversation_keeps_restores_and_clears_its_own_draft():
    """每个会话各自保存、恢复和清除输入框草稿

    切换会话时立即写入当前草稿，切回时恢复文字和已选引用；同一 Chat 的另一个视图能看到尚未写入的编辑；重新挂载后读到已保存的草稿；输入框清空且没有引用时不留草稿，只剩引用时仍保留；发送失败时恢复输入并保留草稿，发送成功后清除草稿。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_workspace_drafts.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

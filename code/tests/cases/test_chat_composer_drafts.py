"""A Chat saves the message being written when it changes, and only then.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_composer_drafts_are_saved_when_they_change():
    """输入框草稿在改动后保存，页面隐藏或离开、切换对话、销毁时立即写入；没有改动的内容不写入

    改动在停顿后保存，页面隐藏、离开页面、切换到别的对话或销毁工作区时立即写入，页面保持可见不写入；载入、隐藏、加载提示、来回切换对话或销毁一份没有改动的草稿不写入，已保存的草稿保持原样；销毁后不再响应页面隐藏或离开；只移动光标时，只有显示的就是已保存的那份草稿才更新它，不覆盖另一个视图写下的较新草稿；清空输入框可以保留草稿，保留后空的输入框不写回；清空并丢弃草稿后不写回；只有存储的草稿与预期一致时才清除，清除后不写回，也可以清除另一个对话的草稿；发送失败时输入框恢复并作为这次发送的草稿保存，重新打开后仍在，这条消息到达后恢复的输入和草稿一起移除且不写回，别的消息到达不影响；恢复后改过的输入是新草稿，旧消息到达时保留；切换对话后才失败的发送保存为它自己对话的草稿，不改动当前显示的输入。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_composer_drafts.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""The ways a Workspace draws its message list, and what follows each.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_each_render_path_binds_what_it_drew_tells_the_host_and_keeps_the_place():
    """首次渲染、强制渲染、重画被编辑的尾部、往前加载历史、追加一条消息，都给画出的附件和技能按钮绑定点击，并按画了多少通知宿主

    首次和强制渲染画出整个列表；内容不变时不画也不通知；编辑最后一条回复只重画尾部，其他消息保留原来的行；往前加载历史时新行加在上方，读者看到的消息留在原位；追加消息时宿主收到“只画了一部分”和变化的行，停在底部的读者跟到新消息，阅读历史的读者不动，已显示的消息不再追加；整体重画时，停在底部的读者仍在底部。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_render_paths.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

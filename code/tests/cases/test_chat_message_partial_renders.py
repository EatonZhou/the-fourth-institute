"""A render of the Chat message list redraws every row its change reaches.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_partial_render_redraws_every_row_its_change_reaches():
    """局部重画保留不受影响的行，并重画所有受影响的行，包括未被修改的消息

    只有实时运行状态变化时补上运行行；回合开始时把最后一个请求之后的回复都展开；新回复到来时原来最新的回复折叠；连续回复组失去最后一条时，组的折叠按钮移到新的末尾；加载的更早消息以回复结尾时，第一条已显示的回复变为接续；Family 工作的第一条回复出现在更早的一页时，标题移到它上面；加载更早消息的同时回合开始，运行行也一起画出。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_partial_renders.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

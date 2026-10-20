"""The production forwarding controller keeps destination loading independent.

:module: resident.conversation
:importance: P1
:design: decision_a6bebf4ac283, decision_2fe6fd5367fb, decision_f560803947c6
"""
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_chatroom_forwarding_is_independent_of_resident_loading():
    """共享转发窗口支持聊天室，隔离加载失败并保留仅 Resident 的分支选择

    Stable case test_7d903f80af50.

    :design-point:
    """
    source = Path(__file__).with_name("javascript") / "message_state_forward.cjs"
    result = subprocess.run(
        ["node", str(source)], input=portal_route_script().decode(),
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "PASS forward-chatroom\n"

"""The composer height follows the splitter, the window and the draft.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_composer_height_follows_splitter_window_and_draft():
    """输入框高度随分隔条、窗口和草稿变化，拖动或按键结束时保存

    输入框从这个 Chat 上次保存的高度开始，高度上限为工作区高度的 46%，同时受宿主给出的最小与最大高度约束；没有保存过时使用宿主给出的高度。向上拖动分隔条让输入框变高，拖动过程中不保存，松开后保存高度并写入草稿；方向键、Home 与 End 移动分隔条并立即保存。窗口变矮时把高度收回新的上限内，不保存；切换到另一个对话时使用那份草稿的高度。不允许调整高度的 Chat 没有分隔条，也不设置高度；销毁后分隔条和窗口大小变化不再改变高度，不保存高度也不写草稿。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_composer_height.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

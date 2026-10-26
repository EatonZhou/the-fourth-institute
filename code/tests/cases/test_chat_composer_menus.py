"""The composer's slash command and mention menus.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_composer_menus_open_navigate_choose_and_close():
    """输入 “/” 和 “@” 时打开斜杠命令菜单与提及菜单，方向键、回车和点击选择，Esc、失焦和销毁关闭

    “/” 列出会话支持的命令并按前缀筛选，方向键循环移动，回车或点击把命令写入输入框并报告一次输入。“@” 立即搜索，之后的关键词编辑在 150 毫秒内合并为一次搜索，被替换的搜索结果丢弃；方向键跳过不可选的候选，回车插入提及标签。Esc 之后同一个 “@” 不再搜索；没有结果和搜索失败分别给出提示并报告错误；输入框失焦时关闭；销毁工作区时关闭菜单并取消尚未绘制的一帧。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_composer_menus.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

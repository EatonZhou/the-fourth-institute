"""How a Resident's messages show: body by kind, footers, request progress, Family controls.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_resident_message_bodies_footers_progress_and_family_controls():
    """Resident 消息的正文、页脚、请求进度和 Family 门面控制

    发送中、发送失败和确认中的消息显示各自状态与操作；随请求读到的 info 折叠在请求里，等待中和已随请求送达的 info 有各自说明；Mailbox 群体消息列出成员，长成员从预览展开；Family 回复只在同一请求的最后一条输出上注明成员，请求未结束时不注明；用量按 M、K 显示；一轮的计划与修改、原生交互和提问消息显示对应卡片；Family 请求进度按状态给出标签和操作；门面的停止只针对运行中和正在路由的请求，改派列出其他成员。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_resident_message_footers.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

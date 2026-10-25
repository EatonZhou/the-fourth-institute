"""A Resident's conversation projected into the Chat's messages and contract.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_resident_conversation_projects_messages_infos_family_requests_and_contract():
    """Resident 对话快照投影为 Chat 消息与契约：协议消息、本地提交、随请求送达的 info、Family 请求进度

    协议消息转成 Chat 消息时角色、作者、文字、附件与各类卡片的替代文字正确，纯附件消息不显示技术说明；本地提交在轨迹收到前只显示一次；随请求读到的 info 折进请求，等待中的 info 和请求未显示的 info 保留；Family 门面的未完成请求把进度挂在最后一条输出下，运行中的输出保持展开；快照的控制、队列、历史分页和运行状态填入 Chat 契约，加载时页面和快照一起读取。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_conversation_projection.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

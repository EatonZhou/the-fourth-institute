"""The Chat of one Resident: session actions and selecting messages to forward.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_resident_chat_session_actions_and_forward_selection():
    """单个 Resident 的 Chat 按后端能力显示压缩按钮，按组选择要转发的消息并交给转发入口

    没有 Resident 时不提供会话操作；只有后端支持 compact 时才显示压缩按钮，其他操作进行中时禁用；清空上下文按钮按状态禁用。点击按钮交给调用方处理。选择请求时连同下一条请求前的最后一条回复一起选中，系统消息不算回复，回复单独选择；取消勾选请求时一并取消其回复。转发选择栏显示所选数量，没有所选时禁用转发，转发按对话顺序交出每条消息的 ID、角色、作者和内容，取消后退出选择。发送中的消息不能开始选择；失去转发能力时原地隐藏 Forward 并结束选择。会话内搜索只查询当前 Resident 并只保留它的消息。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_resident_workspace.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

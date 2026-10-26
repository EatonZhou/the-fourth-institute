"""The Session restart dialog and the progress line above the Chat.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_session_restart_dialog_tracks_cancels_and_retries():
    """Session 重启对话框按目标提交一次请求，跟踪受理的迁移，可以取消和重试

    对话框打开时显示当前会话绑定快照与当前账号；不支持 Fork 的 Provider 放弃原生 Fork 方案；没有 Session 时只保存 Provider 或账号。提交只发出一次请求，带所选目标和幂等键，请求进行中锁定表单；受理后跟踪迁移进度，取消请求只发一次，迁移结束后关闭对话框并停止观察。受理结果未确认时沿用同一个幂等键或经服务重试同一请求，被拒绝时换新键；失败的迁移以原目标重新打开。需要选择模型时读取目标路由账号的模型目录，带上所选模型和推理强度。聊天顶部的简短进度行在失败时可以确认已知悉，同一页面的其他 Chat 一并隐藏，新的失败再次显示。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_session_restart.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

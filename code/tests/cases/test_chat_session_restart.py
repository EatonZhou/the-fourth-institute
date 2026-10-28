"""The Session restart dialog and the progress line above the Chat.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_session_restart_dialog_tracks_cancels_and_retries():
    """按目的编辑参数、能力和会话；沿用正式回执、取消、重试与版本校验

    相同稳定账号不创建操作，切换保留上下文并等待在途工作，不复发失败输入。
    参数和能力分别保存，后台刷新不覆盖草稿；版本冲突拒绝覆盖新设置。
    显式重新开始允许 preserve/fresh，受理后锁定操作并可取消或重试同一待确认请求。
    无法完整保留上下文的目标禁止默认提交，必须明确选择有损交接或空白开始。
    未知交付不标失败，完成操作退出旧失败横幅。
    """
    for script in ["chat_session_restart.cjs", "resident_setup_protocol.cjs", "resident_setup_contract.cjs"]:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("javascript") / script)],
            input=json.dumps({"routeScript": portal_route_script().decode()}),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

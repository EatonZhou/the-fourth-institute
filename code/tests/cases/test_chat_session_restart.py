"""The Session restart dialog and the progress line above the Chat.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess
import pytest

from management_portal.resources.portal import portal_route_script
from management_portal.hatg.catalog.resident import DECLARATIONS


def test_session_restart_dialog_tracks_cancels_and_retries():
    """按目的编辑参数、能力和会话；沿用正式回执、取消、重试与版本校验

    相同稳定账号不创建操作，切换按现行 ISA 发送摘要重组请求并等待在途工作。
    参数和能力分别保存，后台刷新不覆盖草稿；版本冲突拒绝覆盖新设置。
    受理后锁定操作并可取消或重试同一待确认请求。首次输入才交接已保存的材料。
    未知交付不标失败，完成操作退出旧失败横幅。
    """
    restart = next(declaration for declaration in DECLARATIONS if declaration.name == "RestartSession")
    fixture = {"routeScript": portal_route_script().decode(), "restartParameters": restart.schema()["parameters"]}
    for script in ["runtime_account_handoff.cjs", "chat_session_restart.cjs", "resident_setup_protocol.cjs", "resident_setup_contract.cjs"]:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("javascript") / script)],
            input=json.dumps(fixture),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_settings_card_layout_and_actions():
    """真实共享弹窗样式下保持尺寸、滚动与退出交互，并验证保存、交接和显式空白重启。"""
    runtime = Path(__file__).resolve().parents[2] / 'management_portal/test_framework/tau_runtime/node_modules/playwright'
    if not runtime.is_dir():
        pytest.skip('requires the Portal browser test runtime')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'resident_runtime_layout.browser.cjs')],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

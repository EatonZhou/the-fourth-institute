"""The Family status view opened from the Chat or mounted in a container.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

import pytest

from management_portal.resources.portal import portal_route_script


def test_family_status_members_quota_intake_and_mounting():
    """Family 状态面板显示成员工作状态、执行账号与周额度，可以切换接单、展开详情和打开成员设置

    成员按快照显示工作中、等待执行、停止中、空闲、失败、迁移中或等待本轮后迁移，执行保护关闭时显示 CLOSED 与原因；周剩余取有效、未过期观测中七天窗口的最小值，过期或无效观测显示未知，余额账号显示余额，成员低于 10%、steward 不高于 10% 时提示；按路由找到成员的账号。摘要统计运行、排队和需关注的成员。迁移中的成员暂停接单与设置；切换接单后重读 Family；详情显示职责、模型、当前任务和错误，快照里缺少的当前消息单独读取。从 Chat 打开时是弹层，挂载时在容器中显示；加载期间的多次刷新合并为一次，关闭后不刷新，关闭时关闭成员的设置对话框，销毁时释放成员订阅。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_family_status.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_family_settings_scroll_stays_inside_content_column():
    """真实菜单外壳和成员表格在窄屏横向滚动、实时刷新时保持菜单边界和阅读位置。"""
    runtime = Path(__file__).resolve().parents[2] / 'management_portal/test_framework/tau_runtime/node_modules/playwright'
    if not runtime.is_dir():
        pytest.skip('requires the Portal browser test runtime')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'family_settings_layout.browser.cjs')],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_family_chat_members_controls():
    """Chat 成员名旁可直接操作 M/S，与已有面板同步；失败可重试，窄屏可访问全部成员。"""
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'family_chat_members.browser.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

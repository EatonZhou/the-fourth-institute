"""Panel-sized Watchboard layout keeps inherited conversations readable.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_watchboard_respects_panel_width_and_collapse():
    """真实 WireGuard 面板在窄分栏、手机和短聊天区为消息与提问卡片保留空间。

    覆盖边缘小标、悬停浮出、点击/触屏、键盘/Esc、独立滚动、固定展开与 Resident 切换清理。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'watchboard_panel_layout.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

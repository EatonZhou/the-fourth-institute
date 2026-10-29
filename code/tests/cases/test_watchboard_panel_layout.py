"""Panel-sized Watchboard layout keeps inherited conversations readable.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_watchboard_respects_panel_width_and_collapse():
    """真实 WireGuard 面板在窄分栏、手机和短聊天区为消息与提问卡片保留空间。

    覆盖实际面板渲染、独立滚动、折叠按钮命中、展开收起偏好和宽屏右侧布局。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'watchboard_panel_layout.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

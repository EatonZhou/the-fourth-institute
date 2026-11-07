"""Panel-sized Watchboard layout keeps inherited conversations readable.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_watchboard_respects_panel_width_and_collapse():
    """Watchboard 默认和恢复旧偏好都不挤占窄聊天区，Telepus 与 WireGuard 共用边缘浮出。

    覆盖默认/已存两种值、刷新/缩放、点击/触屏、键盘/Esc、独立滚动、宽屏固定展开与清理。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'watchboard_panel_layout.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

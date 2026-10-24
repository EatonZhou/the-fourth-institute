"""Panel-sized Watchboard layout keeps inherited conversations readable.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_watchboard_respects_panel_width_and_collapse():
    """窄分栏 Watchboard 保持聊天宽度并响应展开、收起及调整大小。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'watchboard_panel_layout.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

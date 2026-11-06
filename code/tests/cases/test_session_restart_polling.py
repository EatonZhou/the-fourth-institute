"""Restart progress queries follow the visible subscriber lifecycle.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_restart_polling_observer_lifetime_and_permanent_errors():
    """关闭/隐藏停读，重新打开复核原请求，永久错误停止且显式恢复可继续。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'session_restart_polling.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

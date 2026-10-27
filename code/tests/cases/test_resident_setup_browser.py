"""Purpose-specific Resident setup controls in one isolated Chromium page.

:module: workbench.chat
:importance: P1
"""
from pathlib import Path
import subprocess


def test_resident_setup_dialog_browser():
    """真实 UI 隔离验证取消、保存、切换、未知交付和窄屏；不访问线上服务。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "resident_setup_protocol.browser.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

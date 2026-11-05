"""Resident quota switch Settings and manual recovery use one shared picker.

:module: workbench.chat
:importance: P1
:design: decision_ccbb1cac24aa, decision_d40f59f5508f
"""
from pathlib import Path
import subprocess


def test_resident_quota_switch_browser():
    """真实浏览器隔离验证开关持久回读、额度提示和复用手选卡片，不修改真实设置或重投消息。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "resident_quota_switch.browser.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

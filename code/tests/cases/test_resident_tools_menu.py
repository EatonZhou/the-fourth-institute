"""工具设置属于当前 Resident，菜单离开操作保护未保存和正在保存的配置。

:module: workbench.panels
:design: decision_8b2fa224616b
:importance: P1
"""
import os
from pathlib import Path
import subprocess


def test_resident_tools_menu(tmp_path):
    script = Path(__file__).with_name('javascript') / 'resident_tools_menu.browser.cjs'
    result = subprocess.run(
        ['node', str(script)], capture_output=True, text=True, timeout=60,
        env={**os.environ, 'PORTAL_TOOL_EVIDENCE': str(tmp_path)},
    )
    assert result.returncode == 0, result.stdout + result.stderr

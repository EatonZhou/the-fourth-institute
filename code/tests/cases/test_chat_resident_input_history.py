"""Resident navigation keeps composer history scoped to the installed target.

:module: workbench.chat
:aspects: behavior
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_empty_composer_recalls_new_resident_history_after_switch():
    """切换期间不缓存旧 Resident 的空历史，空白输入框上下箭头恢复新会话输入及草稿。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'chat_resident_input_history.browser.cjs'
    result = subprocess.run(['node', str(script)],
                            input=json.dumps({'routeScript': portal_route_script().decode()}),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

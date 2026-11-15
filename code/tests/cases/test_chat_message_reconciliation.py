"""Chat repairs missed tail notifications using normal state calibration.

:module: workbench.chat
:aspects: behavior
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_saved_replies_appear_without_sending_another_input():
    """漏收末尾推送后，焦点和定期校准自动补显回复，保留未发送草稿。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'chat_message_reconciliation.browser.cjs'
    result = subprocess.run(['node', str(script)],
                            input=json.dumps({'routeScript': portal_route_script().decode()}),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

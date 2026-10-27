"""Chat readiness must use native identity and loading fields, not message text.

:module: lineage.hatg
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_native_chat_readiness_ignores_message_identity_and_loading_text():
    """原生头部身份、加载状态和可编辑输入框限定当前Chat，正文不能误匹配就绪条件"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_chat_ready_evidence.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

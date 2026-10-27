"""Home's one-shot entry URL is distinct from visible Chat readiness.

:module: resident.conversation
:importance: P1
"""
import os
from pathlib import Path
import subprocess
import sys


def test_home_navigation_identity_survives_native_entry_consumption():
    """原生页面消费入口参数后按稳定身份核导航，完整URL不作为Chat就绪条件"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_home_chat_navigation.cjs")],
        env={**os.environ, "TFI_TEST_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

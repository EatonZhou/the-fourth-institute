"""Home unread prerequisites must identify the current completed input/Final.

:module: resident.conversation
:importance: P1
"""
import os
from pathlib import Path
import subprocess
import sys


def test_home_unread_prerequisites_follow_native_completion_and_activity():
    """原生 Home 的当前完成标记、空闲状态和未读图标必须分别就绪"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_home_unread_prerequisites.cjs")],
        env={**os.environ, "TFI_TEST_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

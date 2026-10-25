"""Watchboard original message and owned Workspace evidence prerequisites.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
import os
from pathlib import Path
import subprocess
import sys


def test_watchboard_native_message_prerequisites():
    """原生序列化文本与展开身份保持稳定，Fork区分独立与共享工作区。

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_watchboard_message_contract.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True,
                            timeout=60, env={**os.environ, 'TAU_PYTHON': sys.executable})
    assert result.returncode == 0, result.stdout + result.stderr

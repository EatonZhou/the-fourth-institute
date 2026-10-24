"""Mailbox original first-frame and response evidence contracts.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def test_mailbox_original_frames_and_global_counts():
    """首帧缺口不可回填，全局计数与分页计数分别保留。

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_mailbox_evidence.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

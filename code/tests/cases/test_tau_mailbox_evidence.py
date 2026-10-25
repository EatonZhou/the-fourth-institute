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


def test_mailbox_original_nested_input_identity():
    """原通知嵌套输入身份不可丢弃，省略、空值和格式错误分别留证。

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_mailbox_event_evidence.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mailbox_original_event_browser_transport():
    """序列化取证在原文档先验依赖，保留删除与完成身份且不制造事件。

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_mailbox_event_transport.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

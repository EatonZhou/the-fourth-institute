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


def test_mailbox_original_read_provenance():
    """原请求绑定真实刷新来源与状态批次，不增加请求或消除原错误。

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_mailbox_read_provenance.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mailbox_original_request_causes_and_navigation_prerequisites():
    """Mailbox 原请求取证保留真实调用来源与导航前置，不发业务请求

    隔离 Chromium 中核对：带修饰的导航名称与折叠的 HATG，原指令与其错误元数据，实际 CDP 调用栈，Page、frame 与 loader 分开记录；登记前的请求被排除，重复身份被拒绝，私有载荷不入证据，停止后的采样不可变。

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_mailbox_request_evidence.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

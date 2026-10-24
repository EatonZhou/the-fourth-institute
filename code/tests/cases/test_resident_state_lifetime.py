"""Resident batch reads retain state through Mailbox subscription replacement.

:module: resident.conversation
:design: decision_18abbdef3da3
"""
from pathlib import Path
import subprocess


def test_resident_batch_lifetime():
    """大于缓存容量的批量状态读取在取消订阅后收敛，不递归重读。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / "resident_state_lifetime.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_resident_state_read_observation():
    """请求来源凭据不改变批量读取、合并、重试及请求正文。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / "resident_state_read_observation.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr

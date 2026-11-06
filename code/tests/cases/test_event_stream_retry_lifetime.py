"""EventSource terminal failures stop automatic replacement requests.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_event_stream_terminal_and_transient_retry_lifetime():
    """终止连接停重连，瞬时失败保恢复，交替订阅失败保前一有效连接。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'event_stream_retry_lifetime.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

"""Original Send/Stop timing without helper reads between native clicks.

:module: operations.test-framework
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def test_native_send_stop_retains_delays_and_failure_without_replay():
    """连续原生发送停止保留禁用等待与失败且不重放

    :design: decision_c953eab654cd
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "tau_send_stop.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

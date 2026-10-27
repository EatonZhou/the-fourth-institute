"""Original Event to Effect connection evidence.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_event_connection_observation():
    """原生连线与连接模式须同次观察，缺端口不能算结束"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_event_connection_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

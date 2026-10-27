"""Original native Function Block card readiness.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_function_block_card_observation():
    """原生折叠块按原ID与可访问名称判断，正文或同名对象不能替代"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_function_block_card_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

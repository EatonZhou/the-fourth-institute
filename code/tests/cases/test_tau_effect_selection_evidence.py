"""Original HATG Effect selection evidence.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_native_effect_selection_observation():
    """原生单击回调完成前不可报告Effect已选中，按原ID保留观察"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_effect_selection_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

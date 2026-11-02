"""Record the original Route save evidence before checking its agreement.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_route_save_records_exact_native_inspector_and_public_counts():
    """保留原生 Inspector 与公开响应，缺材料和补记不产生判定。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "tau_route_limit_evidence.cjs")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

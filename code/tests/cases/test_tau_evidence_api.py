"""TAU 稳定证据入口在业务操作前提供明确且唯一的可调用函数。"""
from pathlib import Path
import subprocess


def test_tau_evidence_entry_forwards_its_authoritative_functions():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_evidence_api.cjs')],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Preserve original HATG snapshot candidates and failures.

:module: lineage.hatg
:design: decision_5b5653c3eaa5
"""
import subprocess
from pathlib import Path


def test_hatg_snapshot_uses_exact_native_root_and_retains_read_errors():
    """嵌入 HATG 的快照按精确根和身份读取，失败仍保留原控件与错误。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "tau_hatg_snapshot_evidence.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

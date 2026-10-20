"""Atomic DOM transaction rollback, separate from browser geometry/iframe tests.

:module: workbench.panels
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_atomic_dom_failure_preserves_tree():
    """PortalWorkbench 面板无法原子移动时拒绝应用新布局，保留原布局、可见性和 DOM 结构"""
    source = Path(__file__).with_name("javascript") / "workbench_transaction_state.cjs"
    result = subprocess.run(["node", str(source), "atomic-failure"], capture_output=True, text=True, timeout=30)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

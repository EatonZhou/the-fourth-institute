"""Exact native Function Block Menu identity and pane evidence.

:module: lineage.hatg
:design: decision_5b5653c3eaa5
"""
import subprocess
from pathlib import Path


def test_function_block_menu_ready_rejects_unrelated_dialog_and_wrong_pane():
    """原生菜单以实例身份与当前分栏就绪，不能由任意可见对话框代替。

    :design-point:
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "tau_function_block_menu_evidence.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

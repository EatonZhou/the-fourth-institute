"""Workbench 恢复安装状态时，显式绑定、打开地址和确认删除的处理顺序。

:module: workbench.panels
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_workbench_installation_state_restores_and_quarantines():
    """同源安装各自隔离状态，打开地址优先于恢复的绑定，只隔离确认删除的 Resident 与 Lineage

    权限不足或暂时失败不隔离；HATG 只隔离当前 GetLineage 返回的 404。
    """
    script = Path(__file__).with_name("javascript") / "workbench_installation_state.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]

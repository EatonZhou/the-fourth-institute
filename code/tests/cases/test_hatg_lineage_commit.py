"""HATG 面板的 Lineage 读取与修改结果都经同一个入口写入画布状态。

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_lineage_commit_keeps_canvas_state_in_step():
    """读取和修改结果经同一入口写入：画布不沿用旧投影，成员变化重新订阅，后台读取让位于进行中的修改"""
    script = Path(__file__).with_name('javascript') / 'hatg_lineage_commit.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

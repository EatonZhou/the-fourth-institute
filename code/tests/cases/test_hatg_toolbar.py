"""The HATG header and toolbar follow the shown Lineage, view and selection.

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_toolbar_actions_follow_lineage_view_and_selection():
    """工具栏按 Lineage、视图与多选显示可用操作，全局 Stop 只在有节点运行时可用，Lineage 列表条目按运行状态更新"""
    script = Path(__file__).with_name('javascript') / 'hatg_toolbar.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

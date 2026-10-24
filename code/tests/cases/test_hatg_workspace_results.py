"""The HATG Workspace results open the file of the Workspace a path names.

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_workspace_result_paths_and_reads():
    """成果路径按所指的 Workspace 打开，过期读取不覆盖当前成果，Workspace 视图只读取缺少的绑定"""
    script = Path(__file__).with_name('javascript') / 'hatg_workspace_results.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

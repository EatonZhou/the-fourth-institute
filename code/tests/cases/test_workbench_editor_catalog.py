"""Editor catalog update is a controller/DOM contract, not a UI refresh command.

:module: workbench.panels
"""
from pathlib import Path
import subprocess


def test_editor_catalog_refresh_preserves_expanded_creation_menu():
    """目录更新保留展开的创建菜单，新条目仍可打开真实编辑器。"""
    script = Path(__file__).with_name('javascript') / 'workbench_editor_catalog.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

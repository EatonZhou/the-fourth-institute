"""Isolated native canvas URL navigation and existing program-object selection."""
from pathlib import Path
import subprocess


def test_lineage_hatg_entry():
    """画布入口展开 HATG 面板并选中 URL 指定的对象，其他入口保留恢复的布局"""
    source = Path(__file__).with_name("javascript") / "lineage_hatg_entry.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

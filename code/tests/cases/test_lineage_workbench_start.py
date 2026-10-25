"""The lineage page loads every script it names and creates its workbench parts."""
from pathlib import Path
import subprocess


def test_lineage_workbench_page_starts():
    """Lineage 页面按 lineage.html 的顺序加载全部脚本，工作台创建各模块部件，首次渲染没有页面错误"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_start.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

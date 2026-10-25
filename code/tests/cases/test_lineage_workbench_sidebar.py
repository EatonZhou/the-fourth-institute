"""The Resident sidebar of the lineage page through production JavaScript."""
from pathlib import Path
import subprocess


def test_lineage_workbench_sidebar():
    """导航列表显示 Which Resident 和个人助手门面、跟随标题并在不可达时移除；缺少对话的 Project 不显示为无权限"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_sidebar.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

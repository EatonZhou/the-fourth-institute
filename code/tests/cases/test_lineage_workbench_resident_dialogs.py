"""The Resident dialogs of the lineage page through production JavaScript."""
from pathlib import Path
import subprocess


def test_lineage_workbench_resident_dialogs():
    """编辑 Resident 对话框显示原名称，拒绝空名称，保存新名称后关闭并重新读取 Resident 列表"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_resident_dialogs.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

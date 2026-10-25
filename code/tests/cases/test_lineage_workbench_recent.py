"""The activity rail's recent Residents on the lineage page through production JavaScript."""
from pathlib import Path
import subprocess


def test_lineage_workbench_recent():
    """最近发出命令的 Resident 按时间倒序出现在快捷栏，移除其中一个只移除它"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_recent.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

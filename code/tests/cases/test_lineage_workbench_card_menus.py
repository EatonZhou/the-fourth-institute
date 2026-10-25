"""The card menus of the lineage page through production JavaScript."""
from pathlib import Path
import subprocess


def test_lineage_workbench_card_menus():
    """Lineage 卡片菜单按服务端权限给出添加与删除，删除 Lineage 先确认，再发送 DeleteLineage 并移除卡片"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_card_menus.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

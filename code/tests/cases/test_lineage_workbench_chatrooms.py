"""The Chatrooms side view of the lineage page through production JavaScript."""
from pathlib import Path
import subprocess


def test_lineage_workbench_chatrooms():
    """聊天室列表先列置顶再按最近活动排序，从卡片菜单置顶会保存并重新排序"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_chatrooms.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

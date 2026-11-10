"""The Chatrooms side view of the lineage page through production JavaScript."""
from pathlib import Path
import subprocess


def test_lineage_workbench_chatrooms():
    """聊天室精确消息链接与站内导航、引用提及、列表置顶和活动排序。"""
    source = Path(__file__).with_name("javascript") / "lineage_workbench_chatrooms.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

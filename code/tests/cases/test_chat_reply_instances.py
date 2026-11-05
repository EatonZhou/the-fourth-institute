"""A shared Family reply retains all request associations with one durable DOM identity."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_family_reply_instances_keep_projected_order():
    """共享回复唯一显示并保留全部关联，刷新、更新与翻页后保持位置和独立折叠。"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_reply_instances.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

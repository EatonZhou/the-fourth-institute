"""A shared Family reply must retain both request positions during DOM reconciliation."""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_family_reply_instances_keep_projected_order():
    """同一 Family 回复显示在多个请求下时，刷新、更新和折叠后仍按投影顺序排列"""
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_reply_instances.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

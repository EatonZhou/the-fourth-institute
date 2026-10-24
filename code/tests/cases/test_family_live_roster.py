"""Family live Chat member list.

:module: lineage.hatg
"""
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_open_roster_tracks_conversation_snapshots(tmp_path):
    """同一 Chat 成员列表随快照增删，加载中到达的更新不丢失。"""
    routes = tmp_path / "routes.js"
    routes.write_bytes(portal_route_script())
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "family_live_roster.cjs"), str(routes)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

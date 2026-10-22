"""WALL native-view geometry and action reachability in constrained host slots."""
from pathlib import Path
import subprocess


def test_wall_viewport_preserves_board_and_action_access():
    """WALL 窄屏与低高度布局

    :module: workflow.wall
    """
    script = Path(__file__).with_name("javascript") / "wall_viewport.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

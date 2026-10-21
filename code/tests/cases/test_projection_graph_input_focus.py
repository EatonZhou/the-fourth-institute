"""Architecture graph loading must not interrupt an adjacent input."""
from pathlib import Path
import subprocess


def test_projection_graph_keeps_input_focus():
    script = Path(__file__).with_name("javascript") / "projection_graph_input_focus.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

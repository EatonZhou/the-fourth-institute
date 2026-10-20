"""Global shortcuts cross both projection sandbox layouts through the host."""
from pathlib import Path
import subprocess


def test_projection_global_shortcuts():
    script = Path(__file__).with_name("javascript") / "projection_global_shortcuts.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

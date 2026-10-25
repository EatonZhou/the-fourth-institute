"""Read-only Watchboard canvas preconditions in an isolated browser."""
from pathlib import Path
import subprocess


def test_watchboard_original_node_preconditions():
    subprocess.run(["node", str(Path(__file__).with_name("javascript") / "tau_watchboard_node_prerequisites.cjs")], check=True)

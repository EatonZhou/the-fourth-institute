"""Recommendation choices use the shared send receipt and retain the selected source."""
from pathlib import Path
import subprocess


def test_handoff_selection_delivery():
    script = Path(__file__).with_name("javascript") / "chat_handoff_selection.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

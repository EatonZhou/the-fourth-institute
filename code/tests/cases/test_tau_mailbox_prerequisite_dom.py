"""Verify the original Pin control without guessed translated labels."""
from pathlib import Path
import shutil
import subprocess
import pytest

def test_mailbox_pin_native_prerequisites():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser evidence checks")
    script = Path(__file__).with_name("javascript") / "tau_mailbox_prerequisite_dom.cjs"
    subprocess.run([node, str(script)], check=True, timeout=60)

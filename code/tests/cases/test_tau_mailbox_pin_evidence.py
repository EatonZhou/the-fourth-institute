"""Exercise exact original Pin evidence with actual concurrent browser traffic."""
from pathlib import Path
import shutil
import subprocess
import pytest

def test_mailbox_pin_original_receipt():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser evidence checks")
    script = Path(__file__).with_name("javascript") / "tau_mailbox_pin_evidence.cjs"
    subprocess.run([node, str(script)], check=True, timeout=90)

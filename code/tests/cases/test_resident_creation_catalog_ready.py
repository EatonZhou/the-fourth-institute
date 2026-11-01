"""Creating a Resident waits for the selected account's real model catalog."""
from pathlib import Path
import subprocess


def test_creation_waits_for_catalog_and_surfaces_read_failure():
    script = Path(__file__).with_name('javascript') / 'resident_creation_catalog_ready.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

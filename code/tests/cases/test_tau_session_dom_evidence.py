"""Check original session readiness against visible Chromium DOM."""
from pathlib import Path
import shutil
import subprocess
import pytest

def test_session_original_dom_evidence():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for browser evidence checks')
    subprocess.run([node, str(Path(__file__).with_name('javascript') / 'tau_session_dom_evidence.cjs')], check=True, timeout=90)

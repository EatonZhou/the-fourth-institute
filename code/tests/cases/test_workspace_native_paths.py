"""Native server paths survive the Resident file-root UI projections.

:module: resident.conversation
:design: decision_8838269736d3
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import subprocess


def test_native_directory_roots():
    script = Path(__file__).with_name('javascript') / 'workspace_native_paths.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

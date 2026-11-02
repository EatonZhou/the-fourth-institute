"""Resident/Lineage file roots use their identities, independently of Workspace bindings.

:module: resident.conversation
:design: decision_ccb99a00eebd
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import subprocess


def test_formal_folder_tree_and_file_opening():
    script = Path(__file__).with_name('javascript') / 'resident_folder_resource_reads.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

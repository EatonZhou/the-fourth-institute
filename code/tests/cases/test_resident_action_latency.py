"""Controlled dependency traces for native Resident actions; no real mutations.

:module: interface.conversation
:importance: P2
"""
from pathlib import Path
import subprocess


def test_resident_action_request_and_readback_dependencies():
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'resident_action_latency.cjs')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

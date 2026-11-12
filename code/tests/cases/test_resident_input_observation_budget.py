"""Single-input observation preserves grouped authority without Chat hydration.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_input_observation_uses_shared_stream_without_chat_history():
    result = subprocess.run(['node', str(Path(__file__).with_name('javascript') /
                            'resident_input_observation_budget.cjs')],
                            capture_output=True, text=True, timeout=20)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr

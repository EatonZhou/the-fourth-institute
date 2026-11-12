"""Frontend delta and private history ownership performance contracts.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_frontend_third_efficiency_contracts():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_name('javascript') / 'third_efficiency.cjs')],
                            capture_output=True, text=True, timeout=30)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr

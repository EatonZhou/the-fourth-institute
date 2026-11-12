"""Projection directory identity and nested mount ownership.

:module: workbench.panels
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('boundary', ['panel', 'identity', 'lifetime'])
def test_projection_boundary_round3(boundary):
    script = Path(__file__).with_name('javascript') / 'projection_boundary_round3.cjs'
    result = subprocess.run(['node', '--test', f'--test-name-pattern=^{boundary} ', str(script)], capture_output=True, text=True, timeout=30)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr

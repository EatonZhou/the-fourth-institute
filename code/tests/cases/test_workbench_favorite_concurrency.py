"""Workbench directory favorite mutation ordering.

:module: workbench.panels
:design: decision_f67ae5e70793
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('boundary', ['toggle', 'failure', 'destroy'])
def test_workbench_favorite_concurrency(boundary):
    script = Path(__file__).with_name('javascript') / 'workbench_favorite_concurrency.cjs'
    result = subprocess.run(['node', '--test', f'--test-name-pattern=^{boundary} ', str(script)], capture_output=True, text=True, timeout=30)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr

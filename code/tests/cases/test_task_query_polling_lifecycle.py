"""Paper registration and report media stop obsolete task queries.

:module: workbench.preview
:design: decision_528bf9985705, decision_e3ced6d30f6b
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case', [
    'paper-401', 'paper-403', 'paper-404', 'paper-transient',
    'paper-close', 'paper-terminal', 'report-401', 'report-403', 'report-404',
    'report-hidden', 'report-destroy', 'report-terminal',
    'paper-query-close', 'report-resume', 'report-validation-hidden',
])
def test_task_query_polling_lifetime(case):
    script = Path(__file__).with_name('javascript') / 'task_query_polling_lifecycle.cjs'
    result = subprocess.run(['node', str(script), case], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

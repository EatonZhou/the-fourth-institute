"""Saved report task polling follows presentation and terminal state.

:module: workbench.preview
:design: decision_6e230393221c
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case', [
    'hidden-inflight', 'hidden-resume', 'resume-denied', 'cancel-inflight',
    'reload-inflight', 'denied-401', 'denied-403', 'denied-404',
    'terminal', 'destroy', 'unpresent', 'abort',
    'cancel-inflight-error', 'hidden-resume-inflight',
])
def test_saved_report_polling_lifecycle(case):
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'home_report_polling.cjs'), case],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

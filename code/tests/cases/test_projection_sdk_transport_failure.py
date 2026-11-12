"""SDK synchronous transport failure cleanup and watch ownership.

:module: workbench.panels
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('boundary', ['request', 'watch'])
def test_projection_sdk_transport_failure(boundary):
    script = Path(__file__).with_name('javascript') / 'projection_sdk_transport_failure.cjs'
    result = subprocess.run(
        ['node', '--test', f'--test-name-pattern=^{boundary} ', str(script)],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr

"""Resource catalog navigation keeps late reads from reopening stale details.

:module: workbench.preview
:design: decision_2f67ed28db67
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('case', [
    'close', 'filter', 'latest', 'stale-error', 'failure-retry',
    'list-close', 'list-detail-failure', 'list-service-unavailable',
])
def test_resource_catalog_detail_lookup_follows_current_navigation(case):
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'resource_catalog_navigation.cjs'), case],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

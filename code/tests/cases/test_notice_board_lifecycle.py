"""Notice Board panel visibility and delayed-read ownership."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('scenario', ['hidden panel', 'destroy during detail', 'destroy during initial list', 'visible detail'])
def test_notice_board_lifecycle(scenario):
    result = subprocess.run(
        ['node', '--test', '--test-name-pattern=' + scenario,
         str(Path(__file__).with_name('javascript') / 'notice_board_lifecycle.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

import subprocess
import os
from pathlib import Path


def test_resident_and_family_reporting_settings():
    result = subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'resident_menu_reporting.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_reporting_menu_readability(tmp_path):
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'resident_menu_reporting.browser.cjs')],
        env={**os.environ, 'REPORTING_MENU_EVIDENCE': str(tmp_path)},
        capture_output=True, text=True, timeout=90,
    )

    assert result.returncode == 0, result.stdout + result.stderr

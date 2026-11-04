import subprocess
import os
from pathlib import Path


def test_resident_and_family_reporting_settings():
    subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'resident_menu_reporting.cjs')],
        check=True, capture_output=True, text=True, timeout=30,
    )


def test_reporting_menu_readability(tmp_path):
    subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'resident_menu_reporting.browser.cjs')],
        env={**os.environ, 'REPORTING_MENU_EVIDENCE': str(tmp_path)},
        check=True, capture_output=True, text=True, timeout=90,
    )

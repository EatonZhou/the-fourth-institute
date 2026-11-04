import subprocess
from pathlib import Path


def test_resident_and_family_reporting_settings():
    subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'resident_menu_reporting.cjs')],
        check=True, capture_output=True, text=True, timeout=30,
    )

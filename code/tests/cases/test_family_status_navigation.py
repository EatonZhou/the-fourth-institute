"""Family member links remain usable during live status updates.

:module: workbench.chat
:aspects: behavior
"""
from pathlib import Path
import subprocess


def test_family_member_navigation_during_refresh():
    """Mouse and keyboard open the exact Resident in popup and embedded views.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'family_status_navigation.browser.cjs'
    subprocess.run(['node', str(script)], check=True, timeout=60)

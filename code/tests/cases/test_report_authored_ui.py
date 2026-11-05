"""Authored report navigation and historical resource compatibility.

:design: decision_e3ced6d30f6b
"""
from pathlib import Path
import subprocess
import pytest


@pytest.mark.parametrize('script', ['home_report_persistence.cjs','home_report_direct.cjs','home_report_quality.browser.cjs','home_dialogue_actions.browser.cjs'])
def test_report_navigation_only_reads_registered_reports(script):
    root=Path(__file__).resolve().parents[2]
    subprocess.run(['node',str(Path(__file__).with_name('javascript')/script)],cwd=root,check=True,timeout=100)

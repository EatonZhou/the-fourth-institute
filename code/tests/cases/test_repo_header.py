"""Repo header composition and keyboard actions in its actual CSS owners.

:module: workflow.repository
:design: decision_6476c69b33da
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import subprocess


def test_repo_header_responsive_navigation_and_capabilities():
    subprocess.run(['node', str(Path(__file__).parent / 'javascript/repo_header.browser.cjs')],
                   check=True, capture_output=True, text=True, timeout=45)

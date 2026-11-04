"""Repo 重命名编辑的冲突与部分完成恢复。

:module: workflow.repository
:design: decision_6476c69b33da
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_editor_retains_draft_and_tracks_confirmed_move_before_retry():
    """旧版本拒绝改名，移动后写失败保留草稿和真实路径。"""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the real editor controller')
    subprocess.run([node, str(Path(__file__).parent / 'javascript/repo_editor_rename.cjs')],
                   check=True, capture_output=True, text=True, timeout=20)


def test_editor_discard_reopen_reads_current_file_in_two_browser_pages():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the real editor controller')
    fixture = Path(__file__).parent / 'javascript/repo_editor_reopen.browser.cjs'
    result = subprocess.run([node, str(fixture)], capture_output=True, text=True, timeout=25)
    if "Executable doesn't exist" in result.stderr or "Cannot find module" in result.stderr:
        pytest.skip('Install Portal test runtime and Chromium for this fixture')
    assert result.returncode == 0, result.stdout + result.stderr

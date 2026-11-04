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

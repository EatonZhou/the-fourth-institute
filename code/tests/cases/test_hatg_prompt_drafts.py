"""Prompt templates fill editable Chat drafts without delivering messages.

:module: lineage.hatg
:importance: P1
"""
import os
from pathlib import Path
import subprocess
import sys


def test_prompt_template_drag_fills_chat_draft():
    """提示词拖入输入框或节点只填草稿，保留附件、撤销和对话隔离，结构模板仍照常施加。"""
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'hatg_prompt_drafts.browser.cjs')],
        env={**os.environ, 'PYTHON': sys.executable}, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

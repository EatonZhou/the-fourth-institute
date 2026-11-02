"""Workspace 原目录响应使用准确目标和同一原请求，不靠后续文件响应补证。"""
from pathlib import Path
import subprocess


def test_workspace_root_read_prompt_observes_original_directory_response():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'tau_workspace_root_read.cjs')],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr

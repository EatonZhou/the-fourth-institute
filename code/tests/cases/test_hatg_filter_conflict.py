"""Rejected Filter drafts remain explicit, versioned retry intents."""
from pathlib import Path
import subprocess


def test_hatg_filter_conflict_draft_contract():
    """Filter 草稿保存遇到版本冲突时保留表单修改并标记冲突，冲突草稿阻止连线操作"""
    source = Path(__file__).with_name('javascript') / 'hatg_filter_conflict.cjs'
    result = subprocess.run(['node', '--test', str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr

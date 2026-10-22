"""Lineage menu members follow formal Resident work state.

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_scope_members_follow_live_state_without_losing_edits():
    """成员菜单订阅正式工作状态，保留选择和草稿，并释放旧订阅"""
    script = Path(__file__).with_name('javascript') / 'scope_member_states.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr

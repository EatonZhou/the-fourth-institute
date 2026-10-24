"""HATG relations: the Workspace Handoff dialog and the relation inspector.

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_handoff_dialog_and_relation_inspector():
    """Handoff 只交接来源真实绑定的 Workspace，按真实指令保存、准备与重试并照实报告准备结果；检查器只允许编辑和删除 Workspace Handoff"""
    script = Path(__file__).with_name('javascript') / 'hatg_relations.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

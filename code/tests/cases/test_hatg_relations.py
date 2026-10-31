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


def test_workspace_projection_keeps_bindings_and_item_level_handoffs():
    """Workspace 投影保留每个 Workspace 身份与全部绑定，Handoff 按条目连到真实端点

    共享 Workspace 列出全部成员，路径取当前值；Handoff 按条目生成边并保留原关系 ID，空条目、非 Handoff 关系与草稿预览不生成边，过期 ID 被排除；投影不修改 Context 坐标和绑定。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name('javascript') / 'workspace_graph_projection.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

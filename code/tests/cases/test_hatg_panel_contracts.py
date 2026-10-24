"""HATG 面板各部分的页面契约：画布卡片的工作状态、Function Block 菜单的工作指南和个人视图位置。

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess

SCRIPTS = Path(__file__).with_name("javascript")
ROOT = Path(__file__).resolve().parents[2]


def _run(name, *arguments, timeout=60):
    result = subprocess.run(["node", *arguments, str(SCRIPTS / name)], cwd=ROOT,
                            capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    return result.stdout


def test_resident_work_state_repaints_canvas_cards():
    """Resident 工作状态的实时事件与正式刷新会重画画布卡片和 Family 卡片

    手动关闭的入口保持关闭，旧事件被拒绝，停止中到空闲的过程按服务端确认显示。
    """
    _run("hatg_resident_work_state.cjs", timeout=120)


def test_family_work_guides_keep_drafts_through_saves_and_conflicts():
    """Function Block 菜单中编辑 Family 工作指南时保留草稿，冲突在原处报告

    保存后重画不丢其他场景的草稿，未知变量被拒绝，版本冲突保留已编辑文本。
    """
    _run("family_work_guides.cjs", "--test")


def test_personal_view_positions_save_per_lineage_in_order():
    """个人视图的 Workspace 位置与相机按 Lineage 依次保存，失败时保留本地调整

    Context 视图的位置不写入个人视图；较晚的保存等待进行中的写入；重新读取前先保存待写的相机。
    """
    _run("workspace_view_positions.cjs")

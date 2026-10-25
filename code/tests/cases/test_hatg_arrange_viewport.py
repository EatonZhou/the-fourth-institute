"""Arrange camera policy against the production D3 graph and toolbar geometry."""
from pathlib import Path
import subprocess


def test_hatg_navigation_resize_preserves_canvas_delivery():
    """HATG 工具栏测量不丢画布 ResizeObserver 通知，销毁取消布局写入。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_navigation_resize.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mounted_hatg_measures_toolbar_until_destroyed():
    """挂载的 HATG 面板按实测工具栏高度留出画布空间，销毁后停止测量。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_mount_layout.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_watchboard_conflict_refreshes_without_replaying_mutation():
    """Watchboard 冲突保留原失败，刷新图和 Chat，不自动重试旧操作。

    :module: resident.conversation
    """
    script = Path(__file__).with_name("javascript") / "watchboard_conflict.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_watchboard_installation_separates_new_cards_only():
    """Watchboard 安装提交后按实测卡片避让，仅保存新增对象位置。

    :module: resident.conversation
    """
    script = Path(__file__).with_name("javascript") / "watchboard_install_layout.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hatg_effect_diagnostics_and_script_remain_readable():
    """Effect 诊断在滚动时保持可见，长脚本软换行而不修改内容。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_effect_inspector_readability.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hatg_mutation_feedback_and_navigation_do_not_cover_canvas():
    """HATG 连续保存状态与宽窄工具栏占独立空间，不遮挡画布标题。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_feedback_layout.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hatg_expansion_and_title_edit_separate_measured_objects():
    """HATG 展开及长标题编辑避让同级对象，保存后重载保持可读。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_object_separation.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_arrange_uses_measured_card_sizes():
    """HATG 紧凑及舒展整理容纳长标题卡片，重载后保持无重叠。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_measured_layout.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hatg_arrange_fits_nodes_below_toolbar():
    """HATG 自动整理后完整显示节点，不被工具栏和窄面板裁切

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_arrange_viewport.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hatg_long_identities_and_reopened_menu_remain_readable():
    """HATG 长标题完整显示，嵌套边界不遮挡成员，菜单重开回到顶部。

    :module: lineage.hatg
    """
    script = Path(__file__).with_name("javascript") / "hatg_identity_readability.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_watchboard_layout_through_lazy_hatg_loader():
    """Watchboard 安装与 Fork 的避让参数穿过真实懒加载代理。

    :module: resident.conversation
    """
    script = Path(__file__).with_name("javascript") / "watchboard_loader_layout.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

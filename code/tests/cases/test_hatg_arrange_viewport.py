"""Arrange camera policy against the production D3 graph and toolbar geometry."""
from pathlib import Path
import subprocess


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

"""Independent DOM contract for stable ID test_0d78eceb488e.

:module: workbench.panels
"""
from pathlib import Path
import subprocess


def test_fixed_collapsed_sibling_selection():
    """固定折叠面板组拒绝同组内容切换

    完整生产Workbench控制器和渲染在jsdom中执行，验证实际select禁用、focus/show拒绝及change不能绕过折叠约束；真实布局、焦点及物理输入仍由浏览器覆盖。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / "workbench_visibility_state.cjs"
    result = subprocess.run(
        ["node", str(source), "fixed-collapsed"],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

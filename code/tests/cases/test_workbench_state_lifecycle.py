"""Workbench layout-state contracts exercised through production JavaScript.

:module: workbench.panels
:design: decision_538e155e1102
"""
from pathlib import Path
import subprocess


def test_hide_show_preserves_prior_split_ratio():
    """工作台面板隐藏后重开保留原分栏比例

    调用实际工作台控制器，核对隐藏移除、恢复方向、面板顺序和原比例；真实DOM、焦点与布局尺寸由保留浏览器用例覆盖。

    Unit implementation proposed for stable case test_59d180cc7313.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / "workbench_state_lifecycle.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

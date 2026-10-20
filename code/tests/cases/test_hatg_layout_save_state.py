"""Layout-save rollback through the actual HATG JavaScript controller.

:design: decision_4adcc1ff2f2c
"""
from pathlib import Path
import subprocess


def test_compact_save_failure_restores_positions_and_next_action():
    """紧凑布局保存失败时恢复位置和下一动作并允许重试

    通过实际HATG控制器、布局投影和保存队列验证失败后位置、密度、下一动作与busy状态恢复，以及重试成功；绘制边界之外的DOM、几何、点击和toast显示由保留浏览器覆盖。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / "hatg_layout_save_state.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=30,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr

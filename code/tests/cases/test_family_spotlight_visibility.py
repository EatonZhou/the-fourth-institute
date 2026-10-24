"""A folded ancestor cannot leave the visible canvas faded by a hidden selection."""
from pathlib import Path
import subprocess


def test_family_spotlight_visibility():
    """嵌套 Family 折叠或刷新后，隐藏门面的选择不淡化可见画布；展开后恢复聚焦。"""
    script = Path(__file__).with_name("javascript") / "family_spotlight_visibility.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

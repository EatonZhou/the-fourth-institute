"""Visible HATG selection, real controller payloads and stale prompt contract."""
from pathlib import Path
import subprocess


def test_visible_canvas_selection_contract():
    """HATG 画布的框选、锚点和 Functionalize 只作用于可见对象，折叠或刷新后的选择不含隐藏成员"""
    source = Path(__file__).with_name('javascript') / 'hatg_canvas_selection.cjs'
    result = subprocess.run(['node', '--test', str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr

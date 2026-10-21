"""Native HATG minimap occlusion and usable viewport contract."""
from pathlib import Path
import subprocess


def test_hatg_minimap_viewport_contract():
    """HATG 画布适配视图时扣除小地图实际遮挡的区域，隐藏或不重叠的小地图不占空间"""
    source = Path(__file__).with_name('javascript') / 'hatg_minimap_viewport.cjs'
    result = subprocess.run(['node', '--test', str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr

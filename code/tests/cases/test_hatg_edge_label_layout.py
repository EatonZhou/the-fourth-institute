"""Crossing Route labels stay readable during canvas geometry changes.

:module: lineage.hatg
"""
from pathlib import Path
import subprocess


def test_crossing_route_labels_survive_geometry_changes():
    """刷新、适配、拖动和调整面板后交叉 Route 标签保持分离。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'hatg_edge_label_layout.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

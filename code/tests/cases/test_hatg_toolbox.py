"""HATG toolbox groups, personal favorites, previews, and preserved drag paths.

:module: lineage.hatg
:importance: P1
:design: decision_13725a7900f6
"""
from pathlib import Path
import subprocess


def test_hatg_toolbox():
    """工具箱分组、收藏、缩略图、模板与原语拖入入口保持一致。"""
    result = subprocess.run(
        ['node', '--test', str(Path(__file__).with_name('javascript') / 'hatg_toolbox.cjs')],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

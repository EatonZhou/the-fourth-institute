"""HATG settles zoom at text/SVG drawing resolution and keeps pointer geometry.

:module: lineage.hatg
:importance: P1
"""
from pathlib import Path
import subprocess


def test_hatg_camera_resolution_and_pointer_geometry():
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'hatg_camera_resolution.browser.cjs')],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

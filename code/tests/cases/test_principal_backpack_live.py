"""Package keeps live Resident associations while its menu remains open.

:module: resident.conversation
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_package_tracks_binding_changes_and_releases_subscription():
    """Workspace 解绑、重绑和刷新更新持有物，切换及销毁释放观察。"""
    script = Path(__file__).with_name('javascript') / 'principal_backpack_live.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

"""Function Block failure acknowledgement follows exact execution identities.

:module: lineage.hatg
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_resident_failure_visibility_and_acknowledgement():
    """块内超时失败可见，新执行重新提醒，取消与旧错误不触发提醒"""
    script = Path(__file__).with_name('javascript') / 'hatg_block_failures.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

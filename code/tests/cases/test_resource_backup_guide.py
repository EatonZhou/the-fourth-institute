"""恢复指南按当前配置显示，不产生指向当前页面的空链接。

:module: operations.deployment
:design: decision_15aab18c0ab3
"""
from pathlib import Path
import subprocess


def test_restore_guide_link_tracks_configuration():
    """在真实页面渲染代码中验证未配置、配置和移除指南后的 DOM。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'resource_backup_guide.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr

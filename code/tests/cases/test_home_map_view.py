"""Home 原生地图适配的生命周期与编辑契约。

:module: home
:importance: P1
"""
import os
from pathlib import Path
import subprocess

import pytest


def test_home_map_native_surfaces(tmp_path):
    """原生地图和线头支持聚焦、编辑回读、草稿恢复、权限重验及取消请求。"""
    code = Path(__file__).resolve().parents[2]
    if not (code / 'management_portal/test_framework/tau_runtime/node_modules/playwright').is_dir():
        pytest.skip('requires Portal browser test runtime')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'home_map_view.browser.cjs')],
        env={**os.environ, 'HOME_MAP_EVIDENCE': str(tmp_path)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

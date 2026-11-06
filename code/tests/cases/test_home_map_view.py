"""Home 原生地图适配的生命周期与编辑契约。

:module: workbench.notifications
:importance: P1
"""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('mode', [[], ['--narrow'], ['--home-narrow']], ids=['native-surfaces', 'narrow-readability', 'home-narrow-hit'])
def test_home_map_native_surfaces(tmp_path, mode):
    """原生项目地图与团队地图支持聚焦、编辑回读、草稿恢复、权限重验及取消请求。"""
    code = Path(__file__).resolve().parents[2]
    if not (code / 'management_portal/test_framework/tau_runtime/node_modules/playwright').is_dir():
        pytest.skip('requires Portal browser test runtime')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'home_map_view.browser.cjs'), *mode],
        env={**os.environ, 'HOME_MAP_EVIDENCE': str(tmp_path)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('mode', ['retry-focus', 'stale-error'])
def test_home_map_recovery_preserves_target_and_current_status(tmp_path, mode):
    """失败重试保留原图层/节点，旧请求清理完成不得覆盖新目标状态。"""
    evidence = Path(os.environ.get('HOME_REVIEW_EVIDENCE', str(tmp_path))) / mode
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'home_map_view.browser.cjs'), '--recovery=' + mode],
        env={**os.environ, 'HOME_MAP_EVIDENCE': str(evidence)},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr

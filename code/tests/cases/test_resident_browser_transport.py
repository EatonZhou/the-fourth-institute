"""Resident 原生浏览器的真实流式传输与认证边界。

:module: security.network-boundary
:design: decision_fb910c43ab07
:profiles: local.fixture
:risk: no-external-side-effects
"""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_native_subscription_delivery_lifetime_and_authentication():
    """真实 Chromium 收到首批、跨30秒续读并取消，拒绝边界保持。"""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the native browser transport')
    fixture = Path(__file__).parent / 'javascript/resident_browser.cjs'
    result = subprocess.run([node, str(fixture)], capture_output=True, text=True, timeout=75)
    if "Executable doesn't exist" in result.stderr or "Cannot find module" in result.stderr:
        pytest.skip('Install the Portal test runtime and Chromium to run this native fixture')
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_large_files_and_redirect_containment():
    """真实附件完整落盘，拒绝/Range/iframe/取消保持原边界。"""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the native browser transport')
    fixture = Path(__file__).parent / 'javascript/resident_browser_files.cjs'
    result = subprocess.run([node, str(fixture)], capture_output=True, text=True, timeout=75)
    if "Executable doesn't exist" in result.stderr or "Cannot find module" in result.stderr:
        pytest.skip('Install the Portal test runtime and Chromium to run this native fixture')
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_popup_attachment_target_and_redirect_containment():
    """工具栏新标签首个请求绑定实际Frame，失败时不释放认证请求。"""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the native browser transport')
    fixture = Path(__file__).parent / 'javascript/resident_browser_popup_files.cjs'
    result = subprocess.run([node, str(fixture)], capture_output=True, text=True, timeout=25)
    if "Executable doesn't exist" in result.stderr or "Cannot find module" in result.stderr:
        pytest.skip('Install the Portal test runtime and Chromium to run this native fixture')
    assert result.returncode == 0, result.stdout + result.stderr

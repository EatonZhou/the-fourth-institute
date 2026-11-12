"""Nested Workbench tab drag cleanup and ownership.

:module: workbench.panels
:design: decision_4348ed990621
:importance: P1
"""
from pathlib import Path
import subprocess


def test_workbench_tab_drag_lifecycle():
    script = Path(__file__).with_name('javascript') / 'workbench_tab_drag_lifecycle.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=60)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr


def test_workbench_tab_drag_native_browser():
    from management_portal.test_framework import slots
    script = Path(__file__).with_name('javascript') / 'workbench_tab_drag.browser.cjs'
    lease = slots.acquire('browser', 1, stop=lambda: False)
    try:
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=90)
        print(result.stdout, end='')
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        lease.release()

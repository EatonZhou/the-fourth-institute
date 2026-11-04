"""Function Block detail reading layout and existing actions.

:module: lineage.hatg
:importance: P1
"""
import os
from pathlib import Path
import subprocess

import pytest


def test_function_block_menu_readability(tmp_path):
    code = Path(__file__).resolve().parents[2]
    if not (code / 'management_portal/test_framework/tau_runtime/node_modules/playwright').is_dir():
        pytest.skip('requires Portal browser test runtime')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'function_block_menu_readability.browser.cjs')],
        env={**os.environ, 'FUNCTION_BLOCK_MENU_EVIDENCE': str(tmp_path)},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Test case detail expansion through the real panel controller.

:module: operations.test-workbench
:aspects: ui, behavior
"""
from pathlib import Path
import subprocess


def test_test_case_prompt_expansion():
    """Task Prompt 展开状态随当前用例保存，切换用例互不覆盖

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "test_case_panel_state.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

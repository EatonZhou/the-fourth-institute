"""Watchboard evidence reads original public graph and script-output contracts."""
from pathlib import Path
import subprocess


def test_watchboard_original_graph_and_output_evidence():
    """原安装身份、残留连线和日志字节按正式公开字段核验。

    :module: operations.test-framework
    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name('javascript') / 'tau_watchboard_evidence.cjs'
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

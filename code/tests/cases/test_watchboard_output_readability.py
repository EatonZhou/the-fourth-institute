"""Watchboard output geometry against the real modal renderer and CSS."""
from pathlib import Path
import subprocess


def test_watchboard_log_output_fits_narrow_and_wide_modals():
    """Watchboard 长日志可读且分页按钮在宽窄弹窗内可见。

    :module: resident.conversation
    """
    script = Path(__file__).with_name("javascript") / "watchboard_output_readability.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

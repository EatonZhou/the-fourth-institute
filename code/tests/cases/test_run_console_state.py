from pathlib import Path
import subprocess


def test_run_console_output_distinguishes_terminal_and_active_runs():
    """运行控制台对已结束和进行中的无输出运行显示不同提示，并保留失败原因"""
    script = Path(__file__).with_name("javascript") / "test_run_console_state.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

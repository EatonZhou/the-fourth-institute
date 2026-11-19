from pathlib import Path
import subprocess


def test_exploration_feedback_source_errors_and_native_confirmation():
    """实际探索应用与宿主隔离联跑：错误不冒充失焦，确认取消不发送。"""
    script = Path(__file__).with_name("javascript") / "projection_exploration_feedback.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr

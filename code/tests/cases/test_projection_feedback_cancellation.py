from pathlib import Path
import subprocess


def test_feedback_cancellation_preserves_reason_without_sending():
    """宿主区分取消确认、切换对象和观察失效，取消后不进入真实发送路径。"""
    script = Path(__file__).with_name("javascript") / "projection_feedback_cancellation.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

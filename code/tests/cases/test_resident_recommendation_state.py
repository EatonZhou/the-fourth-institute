from pathlib import Path
import subprocess


def test_recommendation_submission_failure_and_refresh_recovery():
    """Resident 推荐请求在派发前失败时页面保留查询并显示实际错误，刷新后恢复该失败且不轮询"""
    script = Path(__file__).with_name("javascript") / "resident_recommendation_state.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

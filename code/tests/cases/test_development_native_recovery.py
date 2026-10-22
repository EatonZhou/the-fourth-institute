from pathlib import Path
import subprocess


def test_native_workspace_loading_and_initialization_recovery():
    """原生工作区等待时显示进度，加载或初始化失败后可以恢复且不残留订阅。"""
    script = Path(__file__).with_name("javascript") / "development_native_recovery.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

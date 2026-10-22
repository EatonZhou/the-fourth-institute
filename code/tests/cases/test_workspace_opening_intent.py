"""Workspace URL navigation through asynchronous workbench initialization."""
from pathlib import Path
import subprocess


def test_workspace_opening_intent():
    """工作区入口跨初始化刷新保留，迟到响应不覆盖用户导航，读取失败可重试"""
    source = Path(__file__).with_name("javascript") / "workspace_opening_intent.cjs"
    result = subprocess.run(
        ["node", "--test", str(source)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

"""Knowledge 收藏入口使用真实共享按钮与 DOM 增强器，网络请求保持隔离。"""
from pathlib import Path
import subprocess


def test_knowledge_favorites_shared_interactions():
    script = Path(__file__).with_name("javascript") / "knowledge_favorites.browser.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

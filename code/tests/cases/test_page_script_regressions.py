"""Pure JavaScript request-owner regressions run without a browser or screenshot."""
from pathlib import Path
import subprocess


def test_page_script_request_owners():
    """Family 操作尊重取消、成员运行设置不重启；Article 与 Repo 请求结果保留各自归属"""
    source = Path(__file__).with_name("javascript") / "page_script_regressions.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

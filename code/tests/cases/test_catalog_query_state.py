from pathlib import Path
import subprocess


def test_catalog_search_failure_retry_and_superseded_responses():
    """测试目录查询失败显示可重试错误，筛选变更立即拒绝过期响应"""
    script = Path(__file__).with_name("javascript") / "test_catalog_query_state.cjs"
    result = subprocess.run(["node", "--test", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

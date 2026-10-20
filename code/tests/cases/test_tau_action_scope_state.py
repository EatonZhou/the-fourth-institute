"""Independent action ownership contract for stable ID test_f0c7b8f3b651.

:module: operations.test-framework
:aspects: functional, behavior
:design: decision_c953eab654cd
"""
from pathlib import Path
import subprocess


def test_action_requests_belong_only_to_the_observed_page_and_ready_interval():
    """TAU动作仅关联当前页面及就绪区间内的请求

    实际观察器、注入文档探针和JSONL验证同URL跨页隔离、异步ready完成、回调结果、回执及脱敏；真实浏览器网络、导航和计时继续由保留入口覆盖。

    :design-point:
    """
    source = Path(__file__).with_name("javascript") / "tau_action_scope_state.cjs"
    result = subprocess.run(
        ["node", str(source), "action-page-scope"],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        "PASS test_f0c7b8f3b651 action-page-scope; HTTP and DOM cleanup complete"
    )

"""Paper asynchronous operations preserve the selected resource and error semantics.

:module: workflow.paper
:feature: paper
:importance: P1
:aspects: functional, behavior
:design: decision_528bf9985705, decision_e37f240e186d
"""
from pathlib import Path
import subprocess


def test_paper_async_selection_and_review_polling():
    """生产 Paper 控制器的延迟响应不跨论文写状态；失效权限不无限轮询。"""
    script = Path(__file__).with_name("javascript") / "paper_async_selection.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

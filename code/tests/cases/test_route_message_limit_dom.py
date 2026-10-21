"""test_c42f3a67d908: Route limit form state through production JS and real DOM.

:module: lineage.hatg
:importance: P1
:aspects: functional, behavior
:design: decision_4adcc1ff2f2c
"""
from pathlib import Path
import subprocess


def test_route_message_limit_defaults_saves_and_resets_without_losing_edits():
    """Route 上限表单保存、重置与持久状态恢复

    真实生产表单与控制器覆盖无限次、零值、失败/成功重置保留草稿及新文档回填；原生重载、物理输入和提示CSS保留在EventSource浏览器契约。

    :design-point:
    """
    source = Path(__file__).with_name("javascript") / "route_message_limit_dom.cjs"
    result = subprocess.run(
        ["node", str(source)], capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

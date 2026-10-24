"""Question cards that request permissions lend exactly the approved ones with the answer.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10
"""
from pathlib import Path
import subprocess


def test_chat_question_credentials():
    """提问卡片展示权限请求，同意时随回答签发正好这些凭证

    卡片列出精确的 A 节点与资源，只提供固定的同意与不交出；同意后 DeliverMessage 带上请求的凭证，不交出不带；
    门面中成员的权限请求不能同意。真实聊天面板与问题卡片，合成快照，DeliverMessage 只被截获。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "chat_question_credentials.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

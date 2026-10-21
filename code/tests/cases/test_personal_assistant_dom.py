"""Personal assistant entry, history, receipts and handoff cards through production JavaScript.

:module: lineage.team
:aspects: ui, behavior
:design: decision_44cf587c1721, decision_3e07c6d35cac
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("script", ["personal_assistant.cjs", "personal_assistant_dom.cjs"])
def test_personal_assistant_javascript_contracts(script):
    """个人助手的入口、历史分页、已读回执和转交卡片按生产脚本工作

    新页面在 Mailbox 旁直接画出上次确认的个人助手入口，再按服务端结果更新；指针、焦点和几何由保留的浏览器用例覆盖。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    source = Path(__file__).with_name("javascript") / script
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

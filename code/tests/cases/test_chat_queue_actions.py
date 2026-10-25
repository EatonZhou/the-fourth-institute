"""The queue above the composer: Merge all and injecting a Family member's input.

:module: workbench.chat
:importance: P1
"""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("script", ["chat_queue_merge_all.cjs", "family_queue_inject.cjs"])
def test_queue_actions_follow_the_mailbox_and_member_state(script):
    """排队区的“合并所有”和 Family 成员输入的“插入”按 Mailbox 与成员状态动作

    合并所有按 Mailbox 全选的顺序提交待处理请求和整组群体消息、不含 info，确认后才刷新，失败可重试，只有一组时不显示；Family 成员排队输入的插入只在成员正在执行、支持插入且可写时可用，重复点击和等待回执期间不重复提交，切换目标后不提交旧目标，失败后可以重试。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / script)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

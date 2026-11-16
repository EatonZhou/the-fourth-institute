"""Mailbox 暂存与队首操作经过真实浏览器和前端 ISA 适配层。

:module: resident.conversation
:design: decision_15f7df737ed5
:importance: P1
"""
from pathlib import Path
import subprocess


def test_mailbox_hold_and_move_first_controls():
    """暂存/恢复、队首、权限、并发错误及聊天入口，不访问生产或真实模型。"""
    script = Path(__file__).with_name("javascript") / "mailbox_hold_controls.browser.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr

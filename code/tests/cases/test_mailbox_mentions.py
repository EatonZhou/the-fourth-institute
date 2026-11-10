"""Global mention alerts use the authoritative User Mailbox projection."""
from pathlib import Path
import subprocess


def test_mailbox_mentions_browser():
    """不打开 Mailbox 也显示用户提及，关闭不读消息，重连去重并隔离身份与权限。"""
    source = Path(__file__).with_name("javascript") / "mailbox_mentions.browser.cjs"
    result = subprocess.run(["node", str(source)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr

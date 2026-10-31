"""Shared Chat mounted in the native WALL template keeps the host's tab labels."""
import os
from pathlib import Path
import subprocess
import sys


def test_shared_chat_keeps_native_wall_tabpanel_labels_across_remounts():
    """共享 Chat 挂进 WALL 原生模板后保留宿主的标签页标题，重复挂载不改写

    Chromium 中用真实 wall.html 模板挂载 Work 与讨论两个 Chat：挂载与重挂后 aria-labelledby 和 tabpanel 角色保持宿主原值，可按标签名找到唯一面板；
    没有名称的宿主获得 Chat 自带标题，宿主自带 aria-label 时不再加标题；本地挂载不发 API 请求，也没有页面错误。

    :module: workbench.chat
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_host_labels.cjs")],
        env={**os.environ, "TFI_TEST_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr

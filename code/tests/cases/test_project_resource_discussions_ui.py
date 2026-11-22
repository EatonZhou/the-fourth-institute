"""Resource discussions select fresh Project context while preserving private history.

:design: decision_58db6282c244
:module: platform.core
"""
import os
from pathlib import Path
import subprocess
import sys


def test_resource_project_adapters_forward_selection():
    """Article、Repo、Knowledge 的读取、发送和设置均透传本次项目选择。"""
    script = Path(__file__).with_name("javascript") / "resource_discussion_project_adapters.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_resource_project_selection_and_private_project_links_in_browser():
    """真实聊天面板逐次准备背景，拒绝旧响应、撤权和失联提交；项目入口分页且不展示聊天正文。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    script = Path(__file__).with_name("javascript") / "resource_discussion_projects.browser.cjs"
    result = subprocess.run(["node", str(script), "--workers=1"], capture_output=True,
                            text=True, timeout=120, env={**os.environ, "TFI_TEST_PYTHON": sys.executable})
    assert result.returncode == 0, result.stdout + result.stderr

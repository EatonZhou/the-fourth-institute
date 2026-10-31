"""The Workbench directory page rendered by its production script in Chromium.

:design: decision_f67ae5e70793
"""
from pathlib import Path
import subprocess


def test_workbench_directory_groups_cards_by_project_and_reads_teams_later():
    """工作台目录按 Project 分组显示卡片，团队状态随后读取，草稿、发布与内置入口分别标注

    真实 portal_workbench_catalog.js 在 Chromium 中渲染：标题与统计行、按 Project 分组及收藏组、打开与设置入口、草稿与已发布版本标记；
    团队信息先显示正在读取，读到后显示成员数、工作与排队数量和活跃度，读取失败时说明暂不可读，内置入口不带发布状态。

    :module: workbench.panels
    """
    script = Path(__file__).with_name("javascript") / "workbench_catalog.cjs"
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr

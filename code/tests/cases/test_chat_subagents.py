"""Subagents in the Chat: live cards, result cards and the detail dialog.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_subagent_live_cards_result_card_and_detail_dialog():
    """子 agent 的实时卡片原位更新，结束后显示结果卡片，详情对话框分页读取完整步骤

    空对话收到运行中的子 agent 时显示实时卡片，按开始时间排列，计时、当前步骤、最近步骤和用量正确；更新只替换变化的部分；结束的子 agent 在结果卡片画出后只通知一次。详情对话框分页读取全部步骤，失败的调用展开，按是否仍在运行显示停止按钮，没有记录时说明；截断的结果从步骤末尾读回全文；保存的 Markdown 和标签页标题计数正确。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_subagents.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

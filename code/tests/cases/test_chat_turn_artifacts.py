"""The plan and changes card of a turn, and answers sent once.

:module: workbench.chat
:importance: P2
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_turn_card_review_diff_editor_and_answers_sent_once():
    """一轮修改卡片的审查按钮只发一次并可重试，Diff 在只读编辑器中按文件查看，回答发送期间不会重复发送

    审查按钮发出一条 turn_review，发送期间和重新绘制后都保持禁用，排队后显示已排队；失败后可重试，重试沿用同一个消息 ID；不能发送或 Diff 被截断时禁用。展开 Diff 时挂载一次只读编辑器，多个文件时提供全部修改与逐个文件的选择，选择文件后显示该文件的 Diff 并可在 Preview 中打开，已删除的文件只能查看 Diff，页面没有 Preview 时报告；资源修改按路径或地址命名并按地址打开。卡片离开页面后释放编辑器，销毁提问卡片和面板时释放全部编辑器。回答完一组问题后在发送期间再次作答不会再发；发送失败时显示原因，可以再次发送到同一个回答位置。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_turn_artifacts.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

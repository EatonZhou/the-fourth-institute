"""The runtime controls of a Resident's Chat.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_resident_chat_runtime_controls_follow_the_resident():
    """Resident 对话的运行设置按钮与 Session 操作：打开设置与重启，显示重启进度，跟随 Resident 并在销毁时释放

    输入框的运行设置按钮显示 Resident 当前的模型与推理强度，点击打开这个 Resident 的运行对话框，关闭后焦点回到按钮。读取 Resident 期间（包括别处改动后重读）按钮不可用，期间重新绘制的按钮同样不可用。对话提供 Session 重启时，顶部按钮打开 Session 重启，不刷新对话；没有时刷新对话。重启进行中顶部显示进度并锁住运行设置按钮，同一 Resident 的新合同不清除进度，进度上的按钮可以打开 Session 重启；重启结束后进度消失、对话刷新一次；失败的重启留在顶部，读者确认后消失，焦点回到顶部按钮。保存设置期间按钮不可用；保存后按钮显示新设置，对话刷新一次并提示从下一轮生效；之后按旧合同重绘输入框，按钮仍显示保存后的设置。宿主更换 Resident 期间运行设置按钮不可用，顶部按钮只刷新；换成另一个 Resident 后不再跟随前一个的重启，显示新 Resident 的设置；前一个 Resident 迟到的结果不改变输入框。读者只能查看的 Resident 按 Backend 声明的字段显示设置，按钮保持不可用；即使它的输入框配置与上一个 Resident 完全相同，也按新 Resident 显示。销毁对话后释放 Resident 并移除进度；没有 Resident 的对话不显示运行设置按钮。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_runtime_controls.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

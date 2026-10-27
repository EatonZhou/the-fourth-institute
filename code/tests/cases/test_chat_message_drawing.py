"""What each draw of a Workspace's message list renders.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_message_list_draws_render_what_changed_and_hand_it_to_the_host():
    """对话消息列表的每种绘制只渲染变化的部分，并把画出的内容交给宿主处理

    宿主的消息正文渲染函数每画一条消息调用一次，测试据此核对每次绘制渲染了哪些消息、它们在对话中的位置，并要求每次变化后的列表与新工作区画出的同一组消息一致。编辑消息从第一条变化的消息及其所在回复组开始重画；回复组失去最后一条回复时从组内第一条回复重画；成为最新回复的消息重画；删除最后一条请求时重画它前面的回复；强制绘制、新的渲染函数、换成另一个 Resident 的对话或另一个对话时全部重画；新的运行活动只更新运行行，不重画消息。往上加载更早的消息时只渲染新加载的消息，以及因此变化的已显示消息：Family 历史的接续处、不再是最新的回复。追加回复时连同所在回复组一起渲染，序号按整个对话计算，工作区的合同保留追加的消息，再次绘制同一组消息时不重画；对话进行中可以追加回复，用户消息、已显示的消息、还没有消息的对话、尚未画出的另一个对话交给整体绘制。追加读者自己的输入后，输入历史重新读取第一页，对话查找列出它。运行行保持顺序、等待阶段和渲染选项，一轮结束后重画其中的回复；子 agent 的实时卡片被结果卡片替换时，无论由哪个入口移除，都通知一次运行结束。画出的消息中，资源与链接交给宿主打开，作者头像得到绑定，图片在图片编辑器中打开，保存的副本加入输入框；编辑器打不开时说明原因。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_drawing.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

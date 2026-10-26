"""Long Chat message bodies fold and keep the reader's place.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_long_message_bodies_fold_and_keep_the_readers_place():
    """长正文按绘制后的高度折叠，读者正在读的内容不被折走，整表绘制和追加一条消息遵守同一套规则

    绘制后测量每段正文的长短，量过的长短在重画时直接画出，没有布局的正文保留原值；正文里的图片加载后重新测量；列表宽度变化时重新测量，读历史时第一条在视野内的消息位置不变，在视野内变长的正文保持展开，宽度没变或列表隐藏时不测量，重新显示时测量，跟随底部时变长的正文折叠；本来展开的最新回复在视野内变长时不被固定为展开，不再是最新且读者看着别处时折叠；展开时正文位置不变，收起时按钮留在指针下，选择对页面上所有 Chat 生效并在重画后保留，离开底部的切换转为读历史；新回复到来时，读者正在读的上一条最新回复保持展开，读者自己收起的保持收起，读者看着别处时它折叠；回合运行中，上一个回合的回复折叠，本回合较早的输出保持展开；以上规则对 setMessages 和 appendMessage 一致；正在发送的消息展开；工作区销毁后不再因宽度变化或图片加载而测量。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_message_folds.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

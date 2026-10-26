"""Ctrl/⌘+Z in a Chat takes back its own pastes and inserted attachments.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_composer_undo_takes_back_the_chats_own_changes():
    """输入框的撤销只撤回 Chat 自己做的粘贴和附件插入，其余交给浏览器

    Ctrl/⌘+Z 撤回 Chat 处理的粘贴，文字和光标回到粘贴前，通知宿主输入已改变，并保存撤回后的草稿；没有可撤回的内容时按键交给浏览器；粘贴后又输入文字或选择了引用，按键交给浏览器；浏览器自己完成的粘贴由浏览器撤销；最多保留最近二十次，从最新的开始撤回；用户手动删掉的粘贴不会再被撤回，之后手动输入的同样文字由浏览器撤销；撤回插入的附件时只移除附件标记和插入时跟在它后面的空格，插入前输入的空格和标记后面的文字保持原样；撤回插入的文件时附件离开托盘，宿主收到附件变化，文件预览被释放，已经上传或之后才上传完成的文件被删除；粘贴后才上传完成的附件，撤回粘贴后仍是已上传状态，输入框里的标记显示上传后的路径和预览，下次发送时带上；附件被删掉后的粘贴是一次独立的粘贴，撤回只撤回这次粘贴；发送失败时输入框和附件恢复，附件回到托盘，已上传的文件保留并随下次发送；清空输入框后没有可撤回的内容，写回同样的文字也不能撤回。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_composer_undo.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

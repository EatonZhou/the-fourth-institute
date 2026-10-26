"""A Chat message's attachments upload, change, go away and release their previews.

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_composer_attachments_upload_change_and_release_their_previews():
    """输入框的附件加入、上传、重试、编辑替换、移除与撤销，并回收 Chat 自己生成的预览地址

    加入文件时输入框里出现附件标记、托盘里出现上传中的附件，图片先用文件本身的预览；上传时合并宿主和对话给的上传上下文，上传中的附件不随消息发送，上传完成后换上服务端预览并回收文件预览，每次变化都通知宿主；同一文件随即再次加入被忽略，超过大小上限的文件被拒绝；上传失败显示错误，托盘里的重试按钮重新上传同一文件；从托盘移除或从输入框删掉标记都会移除附件并删除已上传的文件，上传中移除时回收预览，上传完成后删除迟到的文件；编辑后的图片上传完成后替换原附件、保留位置并记住被替换的图片，上传失败时恢复原附件，上传完成时已切换到别的对话则删除这次上传；对话选段作为标记加入，没有托盘项也不上传，空选段不加入；撤销收回刚加入的附件并删除其上传；宿主可以整体设置或修改附件；清空输入框回收被清掉附件的预览，销毁工作区回收它仍持有的全部预览。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_composer_attachments.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

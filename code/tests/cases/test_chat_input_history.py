"""The composer's Arrow history reads what the person typed from the server (sample_admin 10-16).

:module: workbench.chat
:importance: P1
"""
import json
from pathlib import Path
import subprocess

from management_portal.resources.portal import portal_route_script


def test_arrow_history_lists_queued_and_sent_inputs_then_the_server_pages():
    """上下箭头历史先列排队和刚发出的输入，再列服务端分页

    输入框按上箭头先取登录者排队中的输入和刚发出、服务端尚未列出的输入，再按需读服务端分页；其他成员的排队消息和输入不进入历史；服务端列出刚发出的输入后只算一条；Chat 出现登录者的新输入时重新读第一页，回复或他人消息不触发读取；每个会话的历史分开，读取失败时报告错误，之后按上箭头重新读取。队列删除后当前页面仍可恢复原文和附件，迟到的空历史页不会清掉它；向下键恢复原草稿，切换会话不串入旧输入。
    """
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("javascript") / "chat_input_history.cjs")],
        input=json.dumps({"routeScript": portal_route_script().decode()}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_control_queue_projection_delete_then_arrow_restores_complete_long_input(tmp_path):
    """真实控制态投影经过队列删除后，上箭头恢复完整长正文及原有附件。"""
    from management_portal.society_service.runtime_read_models import execution_control_state
    from management_portal.society_service.resident_execution_work import _conversation_queue_item
    from tests.cases.test_conversation_projection_authority import RESIDENT, _message, _repository

    repository = _repository(tmp_path)
    text = '中文长消息🙂\n逐行检查0123456789\n' * 20_000 + '[完整末尾END]'
    message = _message('mine-last', text, '2026-09-14T00:00:00Z')
    repository.append_message(RESIDENT, message)
    queued = {'user_message_id': 'mine-last', 'input_message': message}
    control = execution_control_state({'queue': [queued]})['queue'][0]
    assert len(control['conversation_queue_preview']['content_preview']) == 500
    public = _conversation_queue_item(repository, RESIDENT, control)
    recovered = '\n'.join(part['text'] for part in public['content_parts'] if part['type'] == 'text')
    assert recovered == text
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'chat_input_history.cjs')],
        input=json.dumps({'routeScript': portal_route_script().decode(),
                         'longQueueText': text, 'projectedQueueText': recovered}),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

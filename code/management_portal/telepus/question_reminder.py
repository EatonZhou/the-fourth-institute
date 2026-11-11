"""Read reminder questions through the recipient's ISA client, only in memory."""
from __future__ import annotations

import json

from management_common.async_questions import decode_question_content, question_set


def read_questions(portal, actor, item):
    # Read the conversation the Mailbox says this User can open. The stable
    # source IDs remain the deduplication key, including Family projections.
    arguments = {'resident_id': item['resident_id'], 'message_id': item['message_id']}
    kind = item['kind']
    if kind in ('question', 'approval'):
        request = portal.call(actor, 'GetResidentRequest', arguments)['request']
        questions = request.get('questions') or []
        if not questions and kind == 'approval':
            # Native approval requests carry an interaction, with questions=[].
            interaction = request.get('interaction') or {}
            params = interaction.get('request') or {}
            title = params.get('reason') or params.get('command')
            if not title:
                paths = [change.get('path') for change in (interaction.get('file_change') or {}).get('changes', [])]
                if paths:
                    title = '修改文件：' + '、'.join(path for path in paths if isinstance(path, str))
                elif params.get('permissions'):
                    title = '请求权限：' + json.dumps(params['permissions'], ensure_ascii=False)
            questions = [{'title': title}] if isinstance(title, str) and title.strip() else []
    elif kind == 'question_set':
        message = portal.call(actor, 'GetMessage', arguments)['message']
        parts = decode_question_content(message.get('content_parts') or [], item['resident_id'], item['message_id'])
        group = question_set({**message, 'content_parts': parts})
        questions = (group or {}).get('questions') or []
    else:
        raise ValueError('Unsupported reminder question kind')
    if not isinstance(questions, list) or not questions:
        raise ValueError('Reminder question content unavailable')
    visible = []
    for question in questions:
        title = question.get('title') or question.get('question')
        if not isinstance(title, str) or not title.strip():
            raise ValueError('Reminder question title unavailable')
        options = question.get('options') or []
        labels = [option.get('label') if isinstance(option, dict) else option for option in options]
        if any(not isinstance(label, str) for label in labels):
            raise ValueError('Invalid reminder question option')
        visible.append({'title': title, 'options': labels})
    return dict(item, questions=visible,
                requires_authorization=kind == 'question_set' and any(q.get('credentials') for q in questions))

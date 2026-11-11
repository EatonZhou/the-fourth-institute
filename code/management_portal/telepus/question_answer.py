"""Interpret a quoted reminder in memory; Portal owns answer admission and state."""
from __future__ import annotations

import re

from management_common.async_questions import decode_question_content, question_set


AUTHORIZATION_HINT = '这个问题要授权，请回 Portal 回答。'


def answer_slot_used(portal, actor, resident, answer_id):
    """Use the same answer slot as the web UI; unavailable is not absent."""
    for instruction, field in (('GetMessage', 'message'), ('GetInputState', 'input_state')):
        try:
            value = portal.call(actor, instruction, {'resident_id': resident, 'message_id': answer_id})[field]
        except Exception as exc:
            if getattr(exc, 'code', '') == 'instruction.not_found':
                continue
            raise
        if field == 'message' and value:
            return True
        if field == 'input_state' and (value.get('state') not in (None, '', 'unknown') or value.get('error')):
            return True
    return False


class AnswerFormatError(ValueError):
    """Only shown privately to the recipient, never logged or persisted."""


def option_labels(question):
    return [item.get('label', '') if isinstance(item, dict) else item
            for item in question.get('options') or []]


def parse_answers(questions, text, *, approval=False):
    text = text.strip()
    if approval:
        choice = text.lower()
        if choice in {'同意', '批准', '通过', '可以', 'yes', 'y'}:
            return 'accept'
        if choice in {'拒绝', '不同意', '不行', 'no', 'n'}:
            return 'decline'
        raise AnswerFormatError('请回复“同意”或“拒绝”。')
    if not questions or len(questions) > 3:
        raise AnswerFormatError('问题较多或内容不可用，请回 Portal 回答。')
    values = {}
    if len(questions) == 1:
        values[1] = text
    else:
        for line_number, line in enumerate(text.splitlines(), 1):
            match = re.fullmatch(r'\s*([1-9][0-9]*)\s+(.+?)\s*', line)
            if not match:
                raise AnswerFormatError(f'第 {line_number} 行格式有误，请使用“问题序号 答案”。')
            index = int(match[1])
            if index > len(questions) or index in values:
                raise AnswerFormatError(f'第 {line_number} 行题号无效或重复。')
            values[index] = match[2]
        missing = [str(i) for i in range(1, len(questions) + 1) if i not in values]
        if missing:
            raise AnswerFormatError('尚未回答第 ' + '、'.join(missing) + ' 题，请整组重新提交。')
    parsed = []
    for index, question in enumerate(questions, 1):
        answer = values[index].strip()
        options = option_labels(question)
        if answer.isdecimal() and 1 <= int(answer) <= len(options):
            answer = options[int(answer) - 1]
        if not answer:
            raise AnswerFormatError(f'第 {index} 题尚未填写。')
        free = question.get('allow_free_text', True) and not question.get('credentials')
        if not free and answer not in options:
            raise AnswerFormatError(f'第 {index} 题请选择：' + '；'.join(f'{i}. {label}' for i, label in enumerate(options, 1)))
        parsed.append(answer)
    return parsed


def reply(adapter, account, message, match):
    """Consume a recognized quote, with only identifiers/outcomes in the audit."""
    actor = account['portal_actor']
    resident, source = match['source_resident_id'], match['source_message_id']
    outcome, reason = 'failed', ''
    submitting = False
    try:
        if match['is_summary']:
            reason = 'incomplete'
            return '这是汇总或未列全问题的提醒，请回 Portal 回答。'
        # Mailbox owns recipient identity, including facade projections. Use its
        # visible resident/message pair, not an inferred member conversation.
        current = adapter.notifications.snapshot(actor)
        if 'question' not in current:
            raise LookupError('question projection unavailable')
        item = current['question'].get(match['key'])
        if not item:
            reason = 'handled'
            return '这个问题已经处理过了。'
        portal = adapter.router.portal
        arguments = {'resident_id': item['resident_id'], 'message_id': item['message_id']}
        text = '\n'.join(str(part.get('text_item', {}).get('text') or '')
                         for part in message.get('item_list') or [] if part.get('type') == 1)
        key = f'weixin-question:{actor}:{account["ilink_bot_id"]}:{message["message_id"]}'
        approval = False
        if item['kind'] == 'question_set':
            original = portal.call(actor, 'GetMessage', arguments)['message']
            parts = decode_question_content(original.get('content_parts') or [], item['resident_id'], item['message_id'])
            group = question_set({**original, 'content_parts': parts})
            if not group or not group.get('answer_message_id'):
                raise LookupError('question set unavailable')
            questions = group.get('questions') or []
            if any(q.get('credentials') for q in questions):
                reason = 'authorization'
                return AUTHORIZATION_HINT
            answer_id = group['answer_message_id']
            if group.get('state', 'open') != 'open' or answer_slot_used(portal, actor, item['resident_id'], answer_id):
                reason = 'handled'
                return '这个问题已经处理过了。'
            parsed = parse_answers(questions, text)
            answer = {'type': 'async_question_answer', 'source_message_id': item['message_id'],
                      'answers': [{'question_index': q['question_index'], 'answer': value}
                                  for q, value in zip(questions, parsed)]}
            submitting = True
            result = portal.call(actor, 'DeliverMessage', {'resident_id': item['resident_id'], 'message': {
                'message_id': answer_id, 'role': 'user', 'content_parts': [
                    {'type': 'text', 'text': '\n'.join(parsed)}, answer]}}, key=key)
            # DeliverMessage can acknowledge asynchronous admission before it
            # validates the answer. Only an actual stored answer is success.
            try:
                admitted = portal.call(actor, 'GetMessage', {'resident_id': item['resident_id'], 'message_id': answer_id})['message']
            except Exception as exc:
                if getattr(exc, 'code', '') != 'instruction.not_found':
                    raise
                admitted = {}
            confirmed = any(p.get('type') == 'async_question_answer' and
                            p.get('source_message_id') == item['message_id'] and p.get('answers') == answer['answers']
                            for p in admitted.get('content_parts') or [])
            result = {'status': 'answered' if confirmed else 'pending'}
        else:
            request = portal.call(actor, 'GetResidentRequest', arguments)['request']
            if request.get('status') != 'pending':
                reason = 'handled'
                return '这个问题已经处理过了。'
            questions = request.get('questions') or []
            approval = request.get('kind') == 'approval'
            parsed = parse_answers(questions, text, approval=approval)
            answers = {'action': parsed} if approval else {
                question['id']: {'answers': [answer]} for question, answer in zip(questions, parsed)}
            submitting = True
            result = portal.call(actor, 'AnswerResidentRequest', {**arguments, 'answers': answers}, key=key)
        if result.get('status') == 'pending':
            outcome = 'pending'
            return '答案已提交，等待确认，请勿重复作答。'
        if result.get('status') not in ('answered', 'declined'):
            reason = 'handled'
            return '这个问题已经处理过了，未再次提交答案。'
        outcome = 'answered'
        title = ' '.join(str(item.get('title') or item['resident_id']).split()).replace('*', '')[:100]
        summary = ['同意' if parsed == 'accept' else '拒绝'] if approval else parsed
        return '✅ 已回答 **' + title + '**\n' + '\n'.join(
            f'{index}. ' + answer[:120] + ('…' if len(answer) > 120 else '')
            for index, answer in enumerate(summary, 1))
    except AnswerFormatError as exc:
        reason = 'format'
        return '未提交：' + str(exc)
    except Exception as exc:
        code = getattr(exc, 'code', '')
        if isinstance(exc, PermissionError) or code == 'instruction.forbidden':
            reason, detail = 'permission', '没有回答此问题的权限'
        elif code == 'instruction.conflict':
            reason, detail = 'handled', '这个问题已经处理过了'
        elif submitting:
            outcome, reason = 'pending', 'unconfirmed'
            return '暂时无法确认答案是否提交，请回 Portal 核对，勿重复作答。'
        else:
            reason, detail = 'unavailable', '无法读取或提交此问题，请稍后重试或回 Portal 回答'
        return '未提交：' + detail + '。'
    finally:
        adapter.router.store.append_audit('question_answer', portal_actor=actor,
            payload={'resident_id': resident, 'message_id': source, 'result': outcome, 'reason': reason})

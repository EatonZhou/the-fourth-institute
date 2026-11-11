"""Chatroom mention reads and quoted replies, through the bound User's ISA client."""
from __future__ import annotations

import hashlib

from .quote_text import quote_text


def fingerprint(text):
    visible = quote_text(str(text or ''))
    return hashlib.sha256(visible.encode()).hexdigest() if visible else ''


def source_message(portal, actor, room, message_id):
    cursor, seen = '', set()
    while True:
        args = {'chatroom_id': room, 'limit': 200}
        if cursor:
            args['cursor'] = cursor
        page = portal.call(actor, 'ListChatroomMessages', args)
        for message in page.get('messages') or []:
            if message.get('message_id') == message_id and not message.get('retracted_at'):
                return message
        cursor = page.get('next_cursor') or ''
        if not cursor or cursor in seen:
            raise LookupError('mention source unavailable')
        seen.add(cursor)


def enrich(portal, actor, item):
    message = source_message(portal, actor, item['chatroom_id'], item['source_message_id'])
    author = message.get('author_id') or ''
    if author.startswith('resident_'):
        author = portal.resident(actor, author).get('title') or author
    visible = []
    for part in message.get('content_parts') or []:
        if part.get('type') == 'text':
            visible.append(quote_text(part.get('text') or ''))
        elif part.get('type') == 'image':
            visible.append('[图片]')
        elif part.get('type') == 'file':
            visible.append('[文件 ' + str(part.get('file_name') or part.get('name') or '附件') + ']')
    excerpt = ' '.join(visible)
    return dict(item, author=author, excerpt=excerpt[:120] + ('…' if len(excerpt) > 120 else ''),
                created_at=message.get('created_at'))


def reply(adapter, account, message, match):
    """A matched reminder always consumes the incoming reply, including failures."""
    if match['is_summary']:
        return '这是一条汇总提醒，请回 Portal 回复，或引用对应的单条 @ 提醒。'
    actor, room = account['portal_actor'], match['chatroom_id']
    outcome, reason = 'failed', ''
    try:
        # Recheck visibility/deletion before posting, without marking anything read.
        source_message(adapter.router.portal, actor, room, match['source_message_id'])
        details = adapter.router.portal.call(actor, 'GetChatroom', {'chatroom_id': room})
        title = (details.get('chatroom') or {}).get('title') or room
        parts, omitted = [], False
        for item in message.get('item_list') or []:
            kind = item.get('type')
            text = ((item.get('text_item') or {}).get('text') if kind == 1 else
                    (item.get('voice_item') or {}).get('text') if kind == 3 else '')
            if text and str(text).strip():
                parts.append({'type': 'text', 'text': str(text)})
            if kind in (2, 4, 5):
                # The bridge's ISA transport has no authenticated binary upload
                # data channel. Do not bypass it by writing Chatroom attachments.
                omitted = True
        if not parts:
            reason = 'unsupported_attachment' if omitted else 'empty'
            return '未发送：目前无法把此附件上传到聊天室，请补充文字或回 Portal 发送。'
        adapter.router.portal.call(actor, 'CreateChatroomMessage', {
            'chatroom_id': room, 'content_parts': parts,
            'quote': {'message_id': match['source_message_id']},
        }, key=f'weixin-chatroom:{actor}:{account["ilink_bot_id"]}:{message["message_id"]}')
        outcome = 'sent'
        receipt = '✅ 已发到 **' + ' '.join(str(title).split()).replace('*', '') + '**'
        if omitted:
            receipt += '\n附件未发送：目前无法从微信上传到聊天室，本次仅发送文字。'
        return receipt
    except Exception as exc:
        code = str(getattr(exc, 'code', ''))
        if isinstance(exc, PermissionError) or code == 'instruction.forbidden':
            reason, detail = 'permission', '没有读取或发言权限'
        elif isinstance(exc, LookupError) or code == 'instruction.not_found':
            reason, detail = 'not_found', '原消息或聊天室已不可用'
        else:
            reason, detail = 'unavailable', '服务暂不可用，请稍后重试'
        return '未发送到聊天室：' + detail + '。'
    finally:
        adapter.router.store.append_audit('chatroom_reply', portal_actor=actor,
            payload={'chatroom_id': room, 'result': outcome, 'reason': reason})

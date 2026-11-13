"""Article review threads: source anchors and transactional, shared discussion state."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
import os
import uuid

from management_common.domain_database import DomainDatabase
from management_portal import article_files

DDL = """
CREATE TABLE IF NOT EXISTS comment_threads (
    article_id TEXT NOT NULL, thread_id TEXT NOT NULL, document JSONB NOT NULL,
    PRIMARY KEY (article_id, thread_id)
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase('article', 'comments', DDL)


def database():
    return _database(os.getenv('PORTAL_DATABASE_URL'), os.getenv('PORTAL_DATABASE_NAMESPACE'))


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 6000:
        raise ValueError('评论须为 1–6000 字符')
    return value.strip()


def _anchor(article_id, payload):
    path = payload.get('path')
    if not isinstance(path, str):
        raise ValueError('请选择源码文件')
    file = article_files.read_article_file(article_id, path)
    content = file.get('content')
    if not isinstance(content, str):
        raise ValueError('只能评论文本文件')
    start, end = payload.get('start'), payload.get('end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError('请先选中需要评论的文本')
    raw = content.encode('utf-16-le')
    if end * 2 > len(raw) or end - start > 8000:
        raise ValueError('选区无效或超过 8000 字符')
    try:
        before = raw[:start * 2].decode('utf-16-le')
        quote = raw[start * 2:end * 2].decode('utf-16-le')
        after = raw[end * 2:].decode('utf-16-le')
    except UnicodeError as exc:
        raise ValueError('选区必须位于完整字符边界') from exc
    if quote != payload.get('quote'):
        raise ValueError('原文已变化，请重新选择文本后评论')
    return {'path': file['path'], 'start': start, 'end': end, 'quote': quote,
            'prefix': before[-80:], 'suffix': after[:80], 'line': before.count('\n') + 1}


def list_threads(article_id):
    with database().read() as connection:
        rows = connection.execute('SELECT document FROM comment_threads WHERE article_id=%s ORDER BY thread_id',
                                  (article_id,)).fetchall()
    return sorted((row[0] for row in rows), key=lambda t: t['created_at'])


def mutate(article_id, actor, payload):
    if not isinstance(payload, dict):
        raise ValueError('评论操作须为对象')
    action = payload.get('action')
    now = datetime.now(timezone.utc).isoformat()
    message = lambda: {'id': uuid.uuid4().hex, 'author': actor, 'text': _text(payload.get('text')), 'created_at': now}
    if action == 'create':
        thread = {'id': uuid.uuid4().hex, 'anchor': _anchor(article_id, payload),
                  'messages': [message()], 'resolved': False, 'created_at': now, 'updated_at': now, 'version': 1}
        with database().transaction() as connection:
            connection.execute('INSERT INTO comment_threads VALUES (%s,%s,%s::jsonb)',
                               (article_id, thread['id'], json.dumps(thread)))
        return thread
    if action not in {'reply', 'resolve', 'reopen', 'edit', 'delete'}:
        raise ValueError('未知评论操作')
    thread_id = payload.get('thread_id')
    if not isinstance(thread_id, str):
        raise ValueError('缺少讨论串')
    with database().transaction() as connection:
        row = connection.execute('SELECT document FROM comment_threads WHERE article_id=%s AND thread_id=%s FOR UPDATE',
                                 (article_id, thread_id)).fetchone()
        if not row:
            raise FileNotFoundError('评论不存在')
        thread = row[0]
        if action != 'reply' and payload.get('version') != thread['version']:
            raise CommentConflict('评论已被更新，请刷新评论后重试')
        if action == 'reply':
            if thread['resolved']:
                raise CommentConflict('请先重新打开已解决的讨论')
            if len(thread['messages']) >= 500:
                raise ValueError('此讨论已达到 500 条回复，请新建评论')
            thread['messages'].append(message())
        elif action in {'resolve', 'reopen'}:
            thread['resolved'] = action == 'resolve'
            thread['resolved_by'] = actor if thread['resolved'] else ''
        else:
            item = next((m for m in thread['messages'] if m['id'] == payload.get('message_id')), None)
            if not item or item.get('deleted'):
                raise FileNotFoundError('评论消息不存在')
            if item['author'] != actor:
                raise PermissionError('只能编辑或删除自己的评论')
            item['text'] = _text(payload.get('text')) if action == 'edit' else ''
            item['deleted'] = action == 'delete'
            item['edited_at'] = now
        thread['version'] += 1
        thread['updated_at'] = now
        connection.execute('UPDATE comment_threads SET document=%s::jsonb WHERE article_id=%s AND thread_id=%s',
                           (json.dumps(thread), article_id, thread_id))
    return thread


class CommentConflict(ValueError):
    pass

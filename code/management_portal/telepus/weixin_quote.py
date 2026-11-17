"""Weixin quote (``ref_msg``) reading, plus an in-memory store for ID-only quotes.

Newer Weixin clients may send a quote with only ``svr_id`` and no quoted
content (see the iLink ``RefMessage`` type). The quoted text then exists only
where the message was handled. Messages linked to Portal resolve through the
bridge links; messages Telepus handled locally (answer attempts, commands,
receipts) are kept here in memory only, bounded and short-lived, so their text
never reaches the bridge database, logs or audit.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from .quote_text import quote_text

UNREADABLE = '[引用了一条消息，内容未能读取]'


def quote_id(ref):
    ref = ref or {}
    return ref.get('svr_id') or (ref.get('message_item') or {}).get('msg_id')


def _item_text(item):
    item = item or {}
    kind = item.get('type')
    text = (item.get('text_item') or {}).get('text')
    if text:
        return str(text)
    voice = (item.get('voice_item') or {}).get('text')
    if voice:
        return str(voice)
    if kind == 4 or item.get('file_item'):
        name = str((item.get('file_item') or {}).get('file_name') or '').strip()
        return f'[文件 {name}]' if name else '[文件]'
    if kind == 2 or item.get('image_item'):
        return '[图片]'
    if kind == 5 or item.get('video_item'):
        return '[视频]'
    return ''


def quote_excerpt(ref):
    """Visible text of a quote: title, then the quoted item and its nested items."""
    ref = ref or {}
    title = str(ref.get('title') or '').strip()
    if title:
        return title
    item = ref.get('message_item') or {}
    return next((text for text in [_item_text(item)] + [_item_text(x) for x in item.get('item_list') or []] if text), '')


def message_text(items):
    """Text of an inbound message's own items (not of what it quotes)."""
    return '\n'.join(text for text in (_item_text(item) for item in items or []) if text)


def quote_shape(ref, excerpt):
    """Structure only (key names, item type, excerpt length) for the quote audit."""
    ref = ref or {}
    item = ref.get('message_item') or {}
    shape = {'keys': sorted(ref), 'excerpt_length': len(excerpt)}
    if isinstance(item, dict) and item:
        shape['item_keys'] = sorted(item)
        shape['item_type'] = item.get('type')
        nested = item.get('item_list') or []
        if nested:
            shape['nested_types'] = [x.get('type') for x in nested if isinstance(x, dict)]
    return shape


def block(text):
    return '> ' + str(text).replace('\n', '\n> ')


class RecentQuotes:
    """Per-chat memory of Telepus-handled messages: local id -> (shown, carried).

    ``shown`` is what the user saw, for text matching when the client sends a
    title; ``carried`` is what a quote of it hands to the recipient.
    """

    def __init__(self, *, limit=200, ttl=7 * 86400, clock=time.time):
        self._chats, self._lock = {}, threading.Lock()
        self.limit, self.ttl, self.clock = limit, ttl, clock

    def remember(self, chat, local_id, shown, carried=None):
        if not local_id or not (shown or carried):
            return
        with self._lock:
            rows = self._chats.setdefault(chat, OrderedDict())
            rows[int(local_id)] = (self.clock(), str(shown or ''), str(carried or shown or ''))
            rows.move_to_end(int(local_id))
            while len(rows) > self.limit:
                rows.popitem(last=False)

    def find(self, chat, local_id, excerpt=''):
        with self._lock:
            rows = self._chats.get(chat) or OrderedDict()
            cutoff = self.clock() - self.ttl
            for key in [k for k, row in rows.items() if row[0] < cutoff]:
                del rows[key]
            row = rows.get(int(local_id)) if local_id else None
            if row:
                return row[2]
            needle = quote_text(excerpt)
            if not needle:
                return ''
            matches = {row[2] for row in rows.values() if needle in quote_text(row[1])}
            return matches.pop() if len(matches) == 1 else ''

"""Weixin quote (``ref_msg``) reading and the per-chat record that resolves quotes.

Weixin clients differ in what a quote carries. Some send ``title`` or the quoted
item; newer ones send only ``svr_id``; some send only ``message_item`` with
``msg_id="0"`` and the quoted message's ``create_time_ms``. So Telepus records,
for every message it receives or sends in a Weixin chat, the message time and
its local ID; for messages it handled itself (commands and their replies,
answer receipts, rejected answer text) it also keeps the text that a quote of
it should carry, because Portal has no copy of them. The record is bridge
data like ``message_links.excerpt``: bounded per chat, kept seven days, never
copied into audit events or logs.
"""
from __future__ import annotations

import time

from .quote_text import quote_text

UNREADABLE = '[引用了一条消息，内容未能读取]'
TIME_TOLERANCE_MS = 3000
RETENTION_DAYS = 7
PER_CHAT_LIMIT = 500

DDL = """
CREATE TABLE IF NOT EXISTS weixin_messages (
 chat_id TEXT NOT NULL, local_id BIGINT NOT NULL, at_ms BIGINT NOT NULL,
 shown TEXT NOT NULL DEFAULT '', carried TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(chat_id, local_id)
);
CREATE INDEX IF NOT EXISTS weixin_messages_time ON weixin_messages(chat_id, at_ms);
CREATE INDEX IF NOT EXISTS weixin_messages_created ON weixin_messages(created_at);
"""


def _valid_id(value):
    if value is None or isinstance(value, bool):
        return ''
    text = str(value).strip()
    # Clients without a server ID send 0; it never identifies one message.
    return '' if text in ('', '0') else text


def quote_id(ref):
    ref = ref or {}
    return _valid_id(ref.get('svr_id')) or _valid_id((ref.get('message_item') or {}).get('msg_id'))


def quote_time(ref):
    """Creation time (ms) of the quoted message, when the client gives one."""
    item = (ref or {}).get('message_item') or {}
    for key in ('create_time_ms', 'update_time_ms'):
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit() and int(value) > 0:
            return int(value)
    return 0


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


def now_ms():
    return int(time.time() * 1000)


class QuoteRecord:
    """Per-chat message times and locally handled text, in the bridge database."""

    def __init__(self, router_store, *, tolerance_ms=TIME_TOLERANCE_MS):
        self.router, self.tolerance_ms = router_store, tolerance_ms

    def remember(self, chat, local_id, shown='', carried='', *, at_ms=None):
        """Record a message's time; text only for messages without a Portal copy.

        A later call for the same message keeps its first time and fills text.
        """
        if not local_id:
            return
        at = int(at_ms) if at_ms else now_ms()
        with self.router.connect() as c:
            c.execute("INSERT INTO weixin_messages(chat_id,local_id,at_ms,shown,carried) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(chat_id,local_id) DO UPDATE SET "
                      "shown=CASE WHEN excluded.shown<>'' THEN excluded.shown ELSE weixin_messages.shown END, "
                      "carried=CASE WHEN excluded.carried<>'' THEN excluded.carried ELSE weixin_messages.carried END",
                      (chat, int(local_id), at, str(shown or ''), str(carried or shown or '')))

    def find(self, chat, local_id, excerpt=''):
        """Carried text by local ID, else by a unique match of the quote excerpt."""
        with self.router.connect() as c:
            if local_id:
                row = c.execute("SELECT carried FROM weixin_messages WHERE chat_id=? AND local_id=? "
                                "AND created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'", (chat, int(local_id))).fetchone()
                if row and row['carried']:
                    return row['carried']
            needle = quote_text(excerpt)
            if not needle:
                return ''
            rows = c.execute("SELECT shown,carried FROM weixin_messages WHERE chat_id=? AND carried<>'' "
                             "AND created_at > CURRENT_TIMESTAMP - INTERVAL '7 days' ORDER BY at_ms DESC LIMIT 200",
                             (chat,)).fetchall()
        matches = {row['carried'] for row in rows if needle in quote_text(row['shown'])}
        return matches.pop() if len(matches) == 1 else ''

    def by_time(self, chat, at_ms, *, exclude=0):
        """The one message closest to ``at_ms`` within tolerance: (local_id, delta_ms) or None."""
        if not at_ms:
            return None
        with self.router.connect() as c:
            rows = c.execute("SELECT local_id, abs(at_ms - ?) AS delta FROM weixin_messages WHERE chat_id=? "
                             "AND at_ms BETWEEN ? AND ? AND local_id<>? ORDER BY delta, local_id LIMIT 2",
                             (int(at_ms), chat, int(at_ms) - self.tolerance_ms, int(at_ms) + self.tolerance_ms,
                              int(exclude or 0))).fetchall()
        if not rows or (len(rows) > 1 and rows[0]['delta'] == rows[1]['delta']):
            return None
        return int(rows[0]['local_id']), int(rows[0]['delta'])

    def prune(self, *, limit=500):
        """Retire rows older than seven days and beyond the per-chat cap, in bounded batches."""
        with self.router.connect() as c:
            expired = c.execute(
                "WITH old AS (SELECT chat_id, local_id FROM weixin_messages "
                "WHERE created_at < CURRENT_TIMESTAMP - INTERVAL '7 days' LIMIT ? FOR UPDATE SKIP LOCKED) "
                "DELETE FROM weixin_messages w USING old WHERE w.chat_id=old.chat_id AND w.local_id=old.local_id",
                (limit,)).rowcount
            capped = c.execute(
                "WITH ranked AS (SELECT chat_id, local_id, row_number() OVER (PARTITION BY chat_id ORDER BY at_ms DESC) AS n "
                "FROM weixin_messages), extra AS (SELECT chat_id, local_id FROM ranked WHERE n > ? LIMIT ?) "
                "DELETE FROM weixin_messages w USING extra WHERE w.chat_id=extra.chat_id AND w.local_id=extra.local_id",
                (PER_CHAT_LIMIT, limit)).rowcount
        return expired + capped


class TimedClient:
    """Wrap an iLink client so every sent message records its time and local ID."""

    def __init__(self, client, record, store, chat):
        self._client, self._record, self._store, self._chat = client, record, store, chat

    def send(self, items, context_token, *, client_id):
        result = self._client.send(items, context_token, client_id=client_id)
        server_id = _valid_id((result or {}).get('message_id') or (result or {}).get('msg_id'))
        if server_id:
            try:
                sent_at = quote_time({'message_item': result}) or now_ms()
                self._record.remember(self._chat, self._store.local_id(self._chat, server_id), at_ms=sent_at)
            except Exception:  # noqa: BLE001 - a missing time only weakens later quote lookup
                pass
        return result

    def __getattr__(self, name):
        return getattr(self._client, name)

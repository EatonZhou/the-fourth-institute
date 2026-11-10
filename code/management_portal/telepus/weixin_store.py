"""Persistent iLink credentials, binding sessions and ordered outbound work."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib

from .router_store import document, now_iso

DDL = """
CREATE TABLE IF NOT EXISTS weixin_accounts (
 portal_actor TEXT PRIMARY KEY, ilink_bot_id TEXT UNIQUE NOT NULL,
 ilink_user_id TEXT NOT NULL, baseurl TEXT NOT NULL, bot_token TEXT NOT NULL,
 get_updates_buf TEXT NOT NULL DEFAULT '', context_token TEXT NOT NULL DEFAULT '',
 context_at TIMESTAMPTZ, typing_ticket TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active', last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS weixin_bindings (
 portal_actor TEXT PRIMARY KEY, session_id TEXT NOT NULL, data JSONB NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP + INTERVAL '10 minutes'
);
CREATE TABLE IF NOT EXISTS outbound_queue (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 chat_id TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'weixin',
 item JSONB NOT NULL, manual BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS platform_message_ids (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 channel TEXT NOT NULL, chat_id TEXT NOT NULL, external_id TEXT NOT NULL,
 UNIQUE(channel, chat_id, external_id)
);
"""


def chat_key(bot_id):
    # Telegram chat IDs are decimal; this namespaced key preserves all existing
    # primary keys and gives every channel/chat pair one unambiguous identity.
    return "weixin:" + bot_id


def init_tables(connection):
    """Additive schema shared by new installs and the coordinated upgrade."""
    connection.executescript(DDL)
    for table in ('chat_state', 'telegram_inputs', 'delivered_messages', 'message_links',
                  'question_sessions', 'callback_tokens', 'attachment_deliveries', 'audit_events'):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS channel TEXT GENERATED ALWAYS AS "
                           "(CASE WHEN left(chat_id,7)='weixin:' THEN 'weixin' ELSE 'telegram' END) STORED")
    connection.execute("ALTER TABLE processed_updates ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'telegram'")
    connection.execute('ALTER TABLE chat_state ADD COLUMN IF NOT EXISTS send_count INTEGER NOT NULL DEFAULT 0')
    connection.execute('ALTER TABLE chat_state ADD COLUMN IF NOT EXISTS send_blocked BOOLEAN NOT NULL DEFAULT FALSE')


class WeixinStore:
    def __init__(self, router_store):
        self.router = router_store

    @contextmanager
    def lock(self, actor):
        """Serialize binding changes, inbound dispatch and outbound sends per user.

        Separate processes share this advisory lock; a stale worker re-reads the
        account after acquiring it before using credentials or Portal identity.
        """
        self.router.init_schema()
        with self.router.connect() as connection:
            key = int.from_bytes(hashlib.sha256(("weixin:" + actor).encode()).digest()[:8], "big", signed=True)
            connection.execute("SELECT pg_advisory_xact_lock(?)", (key,))
            yield

    def account(self, actor):
        self.router.init_schema()
        with self.router.connect() as c:
            row = c.execute("SELECT * FROM weixin_accounts WHERE portal_actor=?", (actor,)).fetchone()
        return dict(row) if row else None

    def accounts(self):
        self.router.init_schema()
        with self.router.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM weixin_accounts WHERE status='active'").fetchall()]

    def public_accounts(self):
        """The shared binding list never loads credentials or scanner identifiers."""
        self.router.init_schema()
        with self.router.connect() as c:
            return [dict(r) for r in c.execute(
                'SELECT portal_actor,status FROM weixin_accounts ORDER BY lower(portal_actor)').fetchall()]

    def save_account(self, actor, result):
        with self.router.connect() as c:
            c.execute("INSERT INTO weixin_accounts(portal_actor,ilink_bot_id,ilink_user_id,baseurl,bot_token) "
                      "VALUES(?,?,?,?,?) ON CONFLICT(portal_actor) DO UPDATE SET "
                      "ilink_bot_id=excluded.ilink_bot_id,ilink_user_id=excluded.ilink_user_id,baseurl=excluded.baseurl,"
                      "bot_token=excluded.bot_token,status='active',get_updates_buf='',context_token='',typing_ticket='',last_error=''",
                      (actor, result['ilink_bot_id'], result['ilink_user_id'], result['baseurl'], result['bot_token']))

    def update(self, actor, **fields):
        allowed = {'get_updates_buf', 'context_token', 'context_at', 'typing_ticket', 'status', 'last_error'}
        if set(fields) - allowed:
            raise ValueError('invalid account fields')
        with self.router.connect() as c:
            c.execute('UPDATE weixin_accounts SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE portal_actor=?',
                      (*fields.values(), actor))

    def binding(self, actor, session_id):
        with self.router.connect() as c:
            c.execute('DELETE FROM weixin_bindings WHERE expires_at < CURRENT_TIMESTAMP')
            row = c.execute('SELECT data FROM weixin_bindings WHERE portal_actor=? AND session_id=?', (actor, session_id)).fetchone()
        return row['data'] if row else None

    def save_binding(self, actor, session_id, data):
        with self.router.connect() as c:
            c.execute('INSERT INTO weixin_bindings(portal_actor,session_id,data) VALUES(?,?,?) '
                      'ON CONFLICT(portal_actor) DO UPDATE SET session_id=excluded.session_id,data=excluded.data,expires_at=excluded.expires_at',
                      (actor, session_id, document(data)))

    def delete_binding(self, actor):
        with self.router.connect() as c:
            c.execute('DELETE FROM weixin_bindings WHERE portal_actor=?', (actor,))

    def local_id(self, chat, external_id):
        """Lossless indirection for arbitrary platform IDs, including uint64 IDs."""
        with self.router.connect() as c:
            row = c.execute("INSERT INTO platform_message_ids(channel,chat_id,external_id) VALUES('weixin',?,?) "
                            "ON CONFLICT(channel,chat_id,external_id) DO UPDATE SET external_id=excluded.external_id RETURNING id",
                            (chat, str(external_id))).fetchone()
        return int(row[0])

    def enqueue(self, chat, item, *, manual=False):
        with self.router.connect() as c:
            # Refresh may re-read an input that was reopened by the budget gate.
            delivery = item.get('delivery')
            key = delivery + ':' + str(item.get('question_session') or 'text') if delivery else ''
            if key and c.execute("SELECT 1 FROM outbound_queue WHERE chat_id=? AND item->>'queue_key'=?", (chat, key)).fetchone():
                return
            if key:
                item = {**item, 'queue_key': key}
            c.execute('INSERT INTO outbound_queue(chat_id,item,manual) VALUES(?,?,?)', (chat, document(item), manual))

    def pending(self, chat, *, manual=False):
        with self.router.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM outbound_queue WHERE chat_id=? AND manual=? ORDER BY id', (chat, manual)).fetchall()]

    def finish(self, row_id):
        with self.router.connect() as c:
            c.execute('DELETE FROM outbound_queue WHERE id=?', (row_id,))

    def budget(self, chat, *, reset=False, spent=False, blocked=False):
        with self.router.connect() as c:
            if reset:
                c.execute('UPDATE chat_state SET send_count=0,send_blocked=FALSE WHERE chat_id=?', (chat,))
            elif spent:
                c.execute('UPDATE chat_state SET send_count=send_count+1 WHERE chat_id=?', (chat,))
            elif blocked:
                c.execute('UPDATE chat_state SET send_blocked=TRUE WHERE chat_id=?', (chat,))
            row = c.execute('SELECT send_count,send_blocked FROM chat_state WHERE chat_id=?', (chat,)).fetchone()
        return (int(row[0]), bool(row[1])) if row else (0, False)

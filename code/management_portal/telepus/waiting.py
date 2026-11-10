"""One durable waiting notice per user-message interval, shared by both transports."""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime

from .runtime import setting

STILL_WORKING = "⏳ 仍在处理，回复后会发给你"


class WaitingNotices:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def _state(self, chat):
        self.store.init_schema()
        key = f"waiting:{chat}"
        lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
        with self.store.connect() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(?)', (lock,))
            row = connection.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            state = json.loads(row['value']) if row else {}
            before = dict(state)
            yield state, connection
            if state != before:
                connection.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                   (key, json.dumps(state)))

    def received(self, chat, generation):
        with self._state(chat) as (state, _):
            if state.get('generation') != str(generation):
                state.clear()
                state.update(generation=str(generation), received_at=time.time())

    def replied(self, chat):
        with self._state(chat) as (state, _):
            state['replied'] = True

    def send_if_due(self, chat, send):
        """Send directly, never enqueue. A budget refusal leaves the notice unsent.

        The chat lock serializes refreshers and incoming resets across processes.
        The callback reports whether the transport actually accepted the notice.
        """
        with self._state(chat) as (state, connection):
            if state.get('sent') or state.get('replied'):
                return False
            row = connection.execute('SELECT created_at FROM telegram_inputs WHERE chat_id=? AND open ORDER BY created_at LIMIT 1',
                                     (chat,)).fetchone()
            if not row:
                return False
            oldest = datetime.fromisoformat(str(row['created_at']).replace('Z', '+00:00')).timestamp()
            since = max(oldest, state.get('received_at', oldest))
            if time.time() - since < max(0, float(setting('still_working_after_seconds', 300))):
                return False
            generation = str(state.get('generation') or row['created_at'])
            if not send(STILL_WORKING, generation):
                return False
            state['sent'] = True
            return True

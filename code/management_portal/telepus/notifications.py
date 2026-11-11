"""Opt-in Weixin reminders; Mailbox alone owns unread and pending-question truth."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime

from .router_store import now_iso
from .weixin_store import WeixinStore, chat_key

CATEGORIES = ('mention', 'question')
DDL = """
CREATE TABLE IF NOT EXISTS notification_preferences (
 portal_actor TEXT NOT NULL, category TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT FALSE, enabled_at TIMESTAMPTZ,
 baseline_ready BOOLEAN NOT NULL DEFAULT FALSE,
 PRIMARY KEY(portal_actor,category)
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
 key TEXT PRIMARY KEY, portal_actor TEXT NOT NULL, category TEXT NOT NULL,
 source_resident_id TEXT NOT NULL DEFAULT '', source_message_id TEXT NOT NULL,
 chatroom_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
 discovered_at DOUBLE PRECISION NOT NULL, sent_at TIMESTAMPTZ,
 invalid_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS notification_pending ON notification_deliveries(portal_actor,status,discovered_at);
"""


def candidates(actor, snapshot):
    """Missing fields are unsupported, never an empty, authoritative category."""
    if not isinstance(snapshot, dict) or any(snapshot.get(k) for k in ('next_cursor', 'next_page_token', 'has_more', 'truncated', 'incomplete')):
        raise ValueError('Incomplete Mailbox snapshot')
    result = {}
    if 'items' in snapshot:
        if not isinstance(snapshot['items'], list):
            raise ValueError('Invalid Mailbox items')
        mentions = {}
        for item in snapshot['items']:
            if item.get('kind') != 'chatroom':
                continue
            for mention in item.get('unread_mentions') or []:
                room, message = item.get('chatroom_id'), mention.get('message_id')
                if not room or not message:
                    raise ValueError('Invalid Mailbox mention identity')
                key = f'{actor}:mention:{room}:{message}'
                mentions[key] = dict(category='mention', chatroom_id=room, source_message_id=message,
                                     ordinal=mention.get('ordinal'), title=item.get('title') or '聊天室')
        result['mention'] = mentions
    if 'pending_requests' in snapshot:
        if not isinstance(snapshot['pending_requests'], list):
            raise ValueError('Invalid Mailbox pending requests')
        questions = {}
        for item in snapshot['pending_requests']:
            resident, message = item.get('source_resident_id'), item.get('source_message_id')
            if not resident or not message:
                raise ValueError('Invalid Mailbox question identity')
            questions[f'{actor}:question:{resident}:{message}'] = dict(
                category='question', source_resident_id=resident, source_message_id=message,
                resident_id=item.get('resident_id') or resident,
                message_id=item.get('message_id') or message, kind=item.get('kind'),
                title=item.get('title') or item.get('resident_title') or '', created_at=item.get('created_at'))
        result['question'] = questions
    return result


class Notifications:
    """All methods run under the existing per-user Weixin lock unless documented."""
    def __init__(self, router_store, portal=None, *, clock=time.time):
        self.router = router_store
        self.store = WeixinStore(router_store)
        self.portal = portal
        self.clock = clock
        # Only the discovery callback may reuse this short-lived, complete read.
        # Nothing here survives a process restart or goes into the bridge DB.
        self._snapshots = {}
        self._discovery = {}

    def preferences(self, actor):
        self.router.init_schema()
        with self.router.connect() as c:
            rows = c.execute('SELECT category,enabled FROM notification_preferences WHERE portal_actor=?', (actor,)).fetchall()
        return {**dict.fromkeys(CATEGORIES, False), **{r['category']: r['enabled'] for r in rows}}

    def update(self, actor, **changes):
        self._snapshots.pop(actor, None)
        if set(changes) - set(CATEGORIES) or any(type(v) is not bool for v in changes.values()):
            raise ValueError('提醒设置必须是 mention/question 布尔值')
        account = self.store.account(actor)
        if any(changes.values()) and (not account or account['status'] != 'active'):
            raise ValueError('请先绑定微信，再开启提醒')
        old = self.preferences(actor)
        enabling = {category for category, enabled in changes.items() if enabled and not old[category]}
        # Capture the baseline before acknowledging the switch. A read failure must
        # leave every preference unchanged; unsupported categories remain deferred.
        current = self.snapshot(actor) if enabling else {}
        with self.router.connect() as c:
            for category, enabled in changes.items():
                if old[category] == enabled:
                    continue
                c.execute('INSERT INTO notification_preferences(portal_actor,category,enabled,enabled_at,baseline_ready) '
                          'VALUES(?,?,?,?,FALSE) ON CONFLICT(portal_actor,category) DO UPDATE SET '
                          'enabled=excluded.enabled,enabled_at=excluded.enabled_at,baseline_ready=FALSE',
                          (actor, category, enabled, now_iso()))
                c.execute("DELETE FROM notification_deliveries WHERE portal_actor=? AND category=? AND status!='sent'",
                          (actor, category))
            self._reconcile(c, actor, {category: current[category] for category in enabling if category in current})
            c.execute('DELETE FROM meta WHERE key=?', ('notification-watch:' + actor,))
        return self.preferences(actor)

    def clear(self, actor):
        self._snapshots.pop(actor, None)
        self._discovery.pop(actor, None)
        with self.router.connect() as c:
            c.execute('DELETE FROM notification_preferences WHERE portal_actor=?', (actor,))
            c.execute('DELETE FROM notification_deliveries WHERE portal_actor=?', (actor,))
            c.execute('DELETE FROM meta WHERE key=?', ('notification-watch:' + actor,))
            c.execute("DELETE FROM outbound_queue WHERE item->>'notification_actor'=?", (actor,))

    def card_sent(self, actor, source):
        resident, message = source.get('resident_id'), source.get('message_id')
        if not resident or not message:
            return
        with self.router.connect() as c:
            c.execute("INSERT INTO notification_deliveries(key,portal_actor,category,source_resident_id,source_message_id,"
                      "status,discovered_at,sent_at,invalid_reason) VALUES(?,?,'question',?,?,'sent',?,?,'card') "
                      "ON CONFLICT(key) DO UPDATE SET status='sent',sent_at=excluded.sent_at,invalid_reason='card'",
                      (f'{actor}:question:{resident}:{message}', actor, resident, message, self.clock(), now_iso()))

    def snapshot(self, actor):
        if self.portal is None:
            from .portal_client import PortalClient
            self.portal = PortalClient(channel='weixin')
        started = self.clock()
        current = candidates(actor, self.portal.call(actor, 'GetMailbox', {'user_id': actor}))
        self._discovery[actor] = {'mailbox_seconds': max(0, self.clock() - started), 'snapshot_started': started}
        return current

    def reconcile(self, actor, current):
        """Only successful, supported category snapshots may change durable state."""
        with self.router.connect() as c:
            self._reconcile(c, actor, current)

    def _reconcile(self, c, actor, current):
        """Also used by update so the switch and its baseline commit together."""
        prefs = c.execute('SELECT * FROM notification_preferences WHERE portal_actor=? AND enabled=TRUE', (actor,)).fetchall()
        for pref in prefs:
            category = pref['category']
            if category not in current:
                continue
            entries = current[category]
            for key, item in entries.items():
                c.execute('INSERT INTO notification_deliveries(key,portal_actor,category,source_resident_id,source_message_id,'
                          'chatroom_id,status,discovered_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                          (key, actor, category, item.get('source_resident_id', ''), item['source_message_id'],
                           item.get('chatroom_id', ''), 'pending' if pref['baseline_ready'] else 'baseline', self.clock()))
            rows = c.execute("SELECT key FROM notification_deliveries WHERE portal_actor=? AND category=? AND status='pending'",
                             (actor, category)).fetchall()
            for row in rows:
                if row['key'] not in entries:
                    c.execute("UPDATE notification_deliveries SET status='invalid',invalid_reason='absent' WHERE key=?", (row['key'],))
            c.execute('UPDATE notification_preferences SET baseline_ready=TRUE WHERE portal_actor=? AND category=?', (actor, category))

    def pending(self, actor):
        with self.router.connect() as c:
            return [dict(r) for r in c.execute("SELECT d.* FROM notification_deliveries d JOIN notification_preferences p "
                "ON p.portal_actor=d.portal_actor AND p.category=d.category WHERE d.portal_actor=? AND p.enabled=TRUE "
                "AND d.status='pending' ORDER BY d.discovered_at,d.key", (actor,)).fetchall()]

    def scan(self, actor, *, on_pending=None):
        """One long watch/read pass; return the retry delay, or None when disabled."""
        # Network wait is outside the send lock. Settings/binding are re-read inside it.
        if not any(self.preferences(actor).values()):
            return
        key = 'notification-watch:' + actor
        state = json.loads(self.router.get_meta(key, '{}'))
        if self.clock() < state.get('retry_at', 0):
            return state['retry_at'] - self.clock()
        try:
            watch_started = self.clock()
            due = not state.get('read_at') or self.clock() - state['read_at'] >= 30
            watch = {}
            if not due:
                # Shorten only at the full-read deadline so quiet streams still
                # get their complete fallback snapshot every thirty seconds.
                timeout = min(25, max(1, math.ceil(30 - (self.clock() - state['read_at']))))
                batch = self.portal.first_batch(actor, 'WatchMailbox', {
                    'user_id': actor, 'after_cursor': state.get('cursor', ''), 'timeout_seconds': timeout})
                watch = batch[-1] if batch else {}
            watch_returned = self.clock()
            current = None
            with self.store.lock(actor):
                lock_acquired = self.clock()
                account = self.store.account(actor)
                if not account or account['status'] != 'active' or not any(self.preferences(actor).values()):
                    return
                # Periodic complete reads also discover a newly supported category field.
                if due or watch.get('keys') or watch.get('snapshot_required') or self.clock() - state['read_at'] >= 30:
                    current = self.snapshot(actor)
                    self.reconcile(actor, current)
                    self._discovery[actor].update(watch_returned=watch_returned,
                        watch_seconds=max(0, watch_returned - watch_started),
                        lock_wait_seconds=max(0, lock_acquired - watch_returned),
                        watch_reset=bool(due or watch.get('snapshot_required')))
                    self.router.append_audit('notification_scan', portal_actor=actor,
                        payload={k: v for k, v in self._discovery[actor].items() if k not in ('snapshot_started', 'watch_returned')})
                    state['read_at'] = self.clock()
                state.update(cursor=watch.get('cursor', state.get('cursor', '')), failures=0, retry_at=0)
                self.router.set_meta(key, json.dumps(state))
                pending = bool(self.pending(actor))
                if pending:
                    self.store.enqueue(chat_key(account['ilink_bot_id']), {'notification_actor': actor})
                    if on_pending is not None and current is not None:
                        self._snapshots[actor] = (self._discovery[actor]['snapshot_started'], current)
            if pending and on_pending is not None:
                try:
                    on_pending(actor)
                finally:
                    self._snapshots.pop(actor, None)
            return 0
        except Exception as exc:
            # No response, exception text, content, credentials or DSN in logs/audit.
            with self.store.lock(actor):
                if any(self.preferences(actor).values()):
                    state['failures'] = min(state.get('failures', 0) + 1, 7)
                    state['retry_at'] = self.clock() + min(300, 2 ** state['failures'])
                    self.router.set_meta(key, json.dumps(state))
                    transient = getattr(exc, 'code', '') == 'instruction.owner_unavailable'
                    if (not transient and state['failures'] == 1) or (transient and state['failures'] == 4):
                        self.diagnostic(actor, exc)
                    return state['retry_at'] - self.clock()

    @staticmethod
    def diagnostic(actor, error):
        import sys
        code = getattr(error, 'code', '')
        safe_code = code if isinstance(code, str) and code.startswith('instruction.') and code.replace('.', '').replace('_', '').isalnum() else ''
        print(f'Telepus notification actor={actor} error_type={type(error).__name__} code={safe_code}', file=sys.stderr, flush=True)

    def prepare(self, actor, available=1):
        """Revalidate immediately before sending; return None to retain the queue."""
        if not any(self.preferences(actor).values()):
            return []
        cached = self._snapshots.pop(actor, None)
        age = self.clock() - cached[0] if cached else None
        reused = cached is not None and 0 <= age < 2
        current = cached[1] if reused else self.snapshot(actor)
        discovery = dict(self._discovery.get(actor, {}))
        self.reconcile(actor, current)
        rows = self.pending(actor)
        ready = [r for r in rows if r['category'] in current and r['key'] in current[r['category']]]
        if not ready:
            return None if rows else []
        from .weixin_format import notification_text, without_links
        from .mention_reply import enrich, fingerprint
        from .question_reminder import read_questions
        keys = [r['key'] for r in ready[:5]]
        items = [current[r['category']][r['key']] for r in ready[:5]]
        source_started = self.clock()
        items = [enrich(self.portal, actor, item) if item['category'] == 'mention'
                 else read_questions(self.portal, actor, item) for item in items]
        source_seconds = max(0, self.clock() - source_started)
        titles = {}
        for item in items[:5]:
            if item['category'] == 'question' and not item['title']:
                resident = item['resident_id']
                if resident not in titles:
                    try:
                        titles[resident] = self.portal.resident(actor, resident).get('title') or resident
                    except Exception:
                        titles[resident] = resident
                item['title'] = titles[resident]
        individual = len(ready) <= 3 and available >= len(ready)
        groups = [([key], [item]) for key, item in zip(keys, items)] if individual else [(keys, items)]
        prepared = []
        for group_keys, group_items in groups:
            text = notification_text(group_items, total=1 if individual else len(ready), summary=not individual)
            hashes = {fingerprint(text)}
            if individual and group_items[0]['category'] == 'mention':
                hashes.add(fingerprint(without_links(group_items[0]['excerpt'])))
            discoveries = {r['key']: r['discovered_at'] for r in ready if r['key'] in group_keys}
            event_delays = []
            watch_delays = []
            for key, item in zip(group_keys, group_items):
                try:
                    event_time = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00')).timestamp()
                    event_delays.append(max(0, discoveries[key] - event_time))
                    if reused:
                        watch_delays.append(max(0, discovery['watch_returned'] - event_time))
                except (KeyError, ValueError, TypeError, AttributeError):
                    pass  # Older source projections may have no creation time.
            prepared.append({'keys': group_keys, 'text': text, 'summary': not individual,
                             'discovered_at': min(discoveries.values()),
                             'event_to_discovery_seconds': max(event_delays) if event_delays else None,
                             'read_timing': {'mailbox_seconds': discovery.get('mailbox_seconds', 0),
                                 'snapshot_reused': reused, 'snapshot_age_seconds': age if reused else 0,
                                 'source_read_seconds': source_seconds,
                                 **({'event_to_watch_seconds': max(watch_delays),
                                     'lock_wait_seconds': discovery['lock_wait_seconds'],
                                     'watch_reset': discovery['watch_reset']} if watch_delays else {})},
                             'fingerprints': sorted(hashes - {''})})
        return prepared

    def timing(self, actor, group, *, prepare_seconds, send_seconds):
        seconds = {'discovery_to_send_seconds': max(0, self.clock() - group['discovered_at']),
                   'prepare_seconds': prepare_seconds, 'send_seconds': send_seconds}
        seconds.update(group.get('read_timing', {}))
        if group['event_to_discovery_seconds'] is not None:
            seconds['event_to_discovery_seconds'] = group['event_to_discovery_seconds']
        self.router.append_audit('notification_latency', portal_actor=actor,
                                 payload={key: value if isinstance(value, bool) else round(value, 4) for key, value in seconds.items()})

    def sent(self, group, external_id):
        with self.router.connect() as c:
            for key in group['keys']:
                c.execute("UPDATE notification_deliveries SET status='sent',sent_at=?,external_message_id=?,"
                          "quote_fingerprints=?::jsonb,is_summary=? WHERE key=? AND status='pending'",
                          (now_iso(), str(external_id or ''), json.dumps(group['fingerprints']), group['summary'], key))

    def quoted(self, actor, quote):
        from .mention_reply import fingerprint
        external = quote.get('svr_id') or (quote.get('message_item') or {}).get('msg_id')
        excerpt = quote.get('title') or (quote.get('message_item') or {}).get('text_item', {}).get('text') or ''
        with self.router.connect() as c:
            base = "SELECT * FROM notification_deliveries WHERE portal_actor=? AND status='sent' AND (category='mention' OR is_summary) "
            rows = c.execute(base + 'AND external_message_id=?', (actor, str(external))).fetchall() if external else []
            if not rows and fingerprint(excerpt):
                rows = c.execute(base + 'AND quote_fingerprints @> ?::jsonb',
                                 (actor, json.dumps([fingerprint(excerpt)]))).fetchall()
            if rows and all(row['is_summary'] for row in rows):
                return dict(rows[0])
            return dict(rows[0]) if len(rows) == 1 else None


def settings(caller, user_id='', changes=None):
    """Shared Web/Watchboard authorization boundary; unrelated readers cannot mutate."""
    from .weixin_binding import WeixinBinding
    binding = WeixinBinding()
    actor = binding.target(caller, user_id)
    notifications = Notifications(binding.store.router)
    with binding.store.lock(actor):
        return notifications.preferences(actor) if changes is None else notifications.update(actor, **changes)

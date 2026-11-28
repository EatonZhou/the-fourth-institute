"""Weixin transport, per-user sequencing and durable budget-aware delivery."""
from __future__ import annotations

import logging

import hashlib
import secrets
import sys
import time
from pathlib import Path

from management_common.portal_paths import path as portal_path
from .router_core import RouterCore
from .router_store import document, now_iso
from .runtime import setting
from .weixin_client import WeixinClient, WeixinError
from .weixin_format import WeixinPresentation, segment_text, split_text
from .weixin_store import WeixinStore, chat_key


LOGGER = logging.getLogger(__name__)


class WeixinAdapter:
    def __init__(self, router=None, *, client_factory=WeixinClient):
        self.router = router or RouterCore(presentation=WeixinPresentation())
        self.router.presentation = WeixinPresentation()
        self.router.portal.channel = 'weixin'
        self.store = WeixinStore(self.router.store)
        from .notifications import Notifications
        self.notifications = Notifications(self.router.store, self.router.portal)
        self._client_factory = client_factory
        self.budget = max(2, int(setting('weixin_send_budget', 8)))
        from .weixin_quote import QuoteRecord
        self.quotes = QuoteRecord(self.router.store)

    def client_factory(self, account):
        """iLink client whose sends record message times for later quotes."""
        from .weixin_quote import TimedClient
        return TimedClient(self._client_factory(account), self.quotes, self.store, chat_key(account['ilink_bot_id']))

    def _failure(self, actor, error):
        code = error.code if isinstance(error, WeixinError) else type(error).__name__
        fields = {'last_error': f'iLink: {code}'}
        if code == -14:
            fields['status'] = 'expired'
            self.notifications.clear(actor)
        self.store.update(actor, **fields)

    def poll_once(self, actor):
        account = self.store.account(actor)
        if not account or account['status'] != 'active':
            return
        try:
            result = self.client_factory(account).request('getupdates', {'get_updates_buf': account['get_updates_buf']})
            with self.store.lock(actor):
                current = self.store.account(actor)
                if not current or current['bot_token'] != account['bot_token'] or current['status'] != 'active':
                    return
                for message in result.get('msgs') or []:
                    self._receive(current, message)
                self.store.update(actor, get_updates_buf=str(result.get('get_updates_buf') or account['get_updates_buf']))
        except Exception as exc:
            # Neither HTTP URLs nor raw responses enter logs/status.
            with self.store.lock(actor):
                current = self.store.account(actor)
                if current and current['bot_token'] == account['bot_token']:
                    self._failure(actor, exc)
            raise

    def _receive(self, account, message):
        actor = account['portal_actor']
        if message.get('from_user_id') != account['ilink_user_id'] or message.get('message_type') != 1:
            return
        external_id = message.get('message_id')
        if not external_id:
            return
        chat = chat_key(account['ilink_bot_id'])
        local_id = self.store.local_id(chat, external_id)
        with self.router.store.connect() as c:
            if c.execute('SELECT 1 FROM processed_updates WHERE update_id=?', (-local_id,)).fetchone():
                return
        started = time.time()
        self.router.store.upsert_chat_state(chat, telegram_user_id=account['ilink_user_id'], portal_actor=actor, chat_type='private')
        self.store.update(actor, context_token=str(message.get('context_token') or account['context_token']), context_at=now_iso())
        self.store.budget(chat, reset=True)
        self.router.waiting.received(chat, f'message:{local_id}')
        from .weixin_quote import message_text
        # Quotes may identify this message only by its time, and carry no text.
        self.quotes.remember(chat, local_id, message_text(message.get('item_list')), at_ms=message.get('create_time_ms'))
        from .mention_reply import reply
        match = next((found for item in message.get('item_list') or []
                      if (quote := item.get('ref_msg')) and (found := self._quoted_reminder(chat, actor, quote, local_id))), None)
        if match:
            if match['category'] == 'question':
                from .question_answer import reply as answer
                carry = {}
                text = answer(self, account, message, match, carry=carry)
                if carry.get('text'):
                    # Quoting the rejected text itself also brings its question along.
                    self.quotes.remember(chat, local_id, message_text(message.get('item_list')), carry['text'])
                self._private_receipt(actor, message['message_id'], text, carried=carry.get('text'))
            else:
                self.respond(actor, {'messages': [{'text': reply(self, account, message, match)}]})
            with self.router.store.connect() as c:
                c.execute("INSERT INTO processed_updates(update_id,processed_at,channel) VALUES(?,?,'weixin') ON CONFLICT DO NOTHING",
                          (-local_id, now_iso()))
            return
        text, reference, quote_block = self._content(account, message)
        local = text.strip().split(' ', 1)[0] in {'/help', '/start', '/where', '/home', '/notify'}
        if quote_block and text.strip() and not text.strip().startswith('/'):
            # An unmatched quote reaches the recipient as text; commands stay commands.
            text = quote_block + '\n\n' + text
        else:
            quote_block = ''
        # respond() drains the durable queue in order, including the new local
        # reply. Avoid a redundant pre-flush and typing roundtrip for local commands.
        if not local:
            self.flush(actor, reminders=False)
        dispatch_started = time.time()
        send_started = dispatch_started
        if text.strip() == '/more':
            self.flush(actor, manual=True, one=True)
        elif text:
            response = self.router.dispatch_message(chat_id=chat, telegram_user_id=account['ilink_user_id'], portal_actor=actor,
                text=text, telegram_message_id=local_id, reply_to_telegram_message_id=reference,
                quote_text=bool(quote_block))
            send_started = time.time()
            self.respond(actor, response)
        else:
            send_started = time.time()
        with self.router.store.connect() as c:
            c.execute("INSERT INTO processed_updates(update_id,processed_at,channel) VALUES(?,?,'weixin') ON CONFLICT DO NOTHING",
                      (-local_id, now_iso()))
        # Timing only, no content: how long Weixin held the message before getupdates
        # returned it, and how long Telepus took to handle it and send the replies.
        created = message.get('create_time_ms')
        waited = f'{started * 1000 - float(created):.0f}' if isinstance(created, (int, float)) and created else '?'
        print(f'Telepus weixin message actor={actor} weixin_wait_ms={waited} handle_ms={(time.time() - started) * 1000:.0f}'
              f' dispatch_ms={(send_started - dispatch_started) * 1000:.0f} send_ms={(time.time() - send_started) * 1000:.0f}',
              file=sys.stderr, flush=True)
        if not local:
            self.typing(actor)

    def _quoted_reminder(self, chat, actor, quote, current):
        """A sent reminder the quote refers to, by ID or excerpt, else by message time."""
        from .weixin_quote import quote_id, quote_time
        found = self.notifications.quoted(actor, quote)
        if found or quote_id(quote):
            return found
        hit = self.quotes.by_time(chat, quote_time(quote), exclude=current)
        external = self.store.external_id(chat, hit[0]) if hit else ''
        return self.notifications.quoted(actor, {'svr_id': external}) if external else None

    def _private_receipt(self, actor, incoming_id, text, carried=None):
        """Answer/option text must never enter the durable outbound queue.

        ``carried`` is what quoting this receipt hands on (the question and the
        user's text after a format failure); it is kept in memory only.
        """
        from .weixin_format import format_text
        account = self.store.account(actor)
        chat = chat_key(account['ilink_bot_id'])
        # A received message has just reset the budget and supplied its context.
        # Long format-error options can be read in Portal; keep one receipt.
        visible = format_text(text)
        if len(visible) > 3700:
            visible = visible[:3700] + '\n内容较长，请回 Portal 查看完整选项。'
        try:
            result = self.client_factory(account).send([{'type': 1, 'text_item': {'text': visible}}],
                account['context_token'], client_id=f'telepus-answer-receipt-{incoming_id}')
        except WeixinError as exc:
            raise WeixinError(exc.code, 'answer_receipt') from None
        except Exception:
            # HTTP exceptions may include the request body; never propagate it.
            raise RuntimeError('Weixin answer receipt unavailable') from None
        self.store.budget(chat, spent=True)
        server_id = (result or {}).get('message_id')
        if server_id:
            self.quotes.remember(chat, self.store.local_id(chat, server_id), visible, carried)

    def _reopen_pending(self, chat):
        for row in self.store.pending(chat):
            item = row['item']
            if item.get('delivery'):
                self.router.store.release_delivery(chat, item['delivery'])
            if item.get('input'):
                self.router.store.reopen_input(item['input'])

    def _content(self, account, message):
        """Return (text, quoted local id, quote block for an unmatched quote)."""
        from .weixin_quote import UNREADABLE, block, quote_excerpt, quote_id, quote_shape, quote_time
        text, reference, quote_block = [], 0, ''
        chat = chat_key(account['ilink_bot_id'])
        current = self.store.local_id(chat, message['message_id']) if message.get('message_id') else 0
        for item in message.get('item_list') or []:
            quote = item.get('ref_msg') or {}
            if quote:
                external_id = quote_id(quote)
                reference = self.store.local_id(chat, external_id) if external_id else 0
                linked = self.router.store.linked_message(chat, reference)
                matched_by = 'id' if linked else 'none'
                excerpt = quote_excerpt(quote)
                if not linked:
                    linked = self.router.store.linked_message_by_text(chat, excerpt)
                    if linked:
                        reference = linked['telegram_message_id']
                        matched_by = 'text'
                carried = self.quotes.find(chat, reference, excerpt) if not linked else ''
                at, delta = quote_time(quote), None
                if not linked and not carried and at:
                    # Some clients identify the quoted message only by its time.
                    hit = self.quotes.by_time(chat, at, exclude=current)
                    if hit:
                        reference, delta = hit
                        matched_by = 'time'
                        linked = self.router.store.linked_message(chat, reference)
                        carried = '' if linked else self.quotes.find(chat, reference)
                if not linked:
                    # Messages Telepus handled itself have no Portal counterpart;
                    # hand the quoted text on rather than dropping it.
                    source = 'record' if carried else 'quote' if excerpt else 'none'
                    quote_block = block(carried or excerpt or UNREADABLE)
                self.router.store.append_audit('quote_received', chat_id=chat, portal_actor=account['portal_actor'],
                    payload={'has_id': bool(external_id), 'id_valid': bool(external_id), 'matched_by': matched_by,
                             **({'time_delta_ms': delta} if delta is not None else {}),
                             **({'quote_time_ms': at} if at else {}),
                             **({} if linked else {'text_source': source}), 'shape': quote_shape(quote, excerpt)})
            kind = item.get('type')
            if kind == 1:
                text.append(str((item.get('text_item') or {}).get('text') or ''))
            elif kind == 3:
                transcript = (item.get('voice_item') or {}).get('text')
                if transcript:
                    text.append(str(transcript))
                else:
                    self.respond(account['portal_actor'], {'messages': [{'text': '没能识别这条语音'}]})
            elif kind in (2, 4, 5):
                media = item.get({2: 'image_item', 4: 'file_item', 5: 'video_item'}[kind]) or {}
                try:
                    raw = self.client_factory(account).download(media)
                    suffix = Path(str(media.get('file_name') or '')).suffix[:16] or ('.jpg' if kind == 2 else '.mp4' if kind == 5 else '.bin')
                    root = portal_path('uploads') / 'resident_chat_images' / 'weixin'
                    root.mkdir(parents=True, exist_ok=True)
                    path = root / (secrets.token_hex(16) + suffix)
                    path.write_bytes(raw)
                    text.append(f"[{'image' if kind == 2 else 'file'}: {path}]")
                except Exception as exc:
                    LOGGER.warning("Weixin attachment download unavailable (%s)", type(exc).__name__)
                    text.append('[附件下载失败]')
        return '\n\n'.join(text), reference, quote_block

    def _render_controls(self, chat, item):
        item = dict(item)
        text = item.get('text') or ''
        attachments, controls = [], []
        for row in (item.pop('reply_markup', None) or {}).get('inline_keyboard') or []:
            for button in row:
                callback = button.get('callback_data') or ''
                payload = self.router.store.get_callback_payload(chat, callback[3:]) if callback.startswith('cb:') else {}
                kind = (payload or {}).get('kind')
                command = '/file' if callback.startswith('att:') else '/full' if kind == 'expand' else '/earlier' if kind == 'history_more' else '/action'
                counter_key = 'weixin-counter:' + chat + ':' + command
                number = int(self.router.store.get_meta(counter_key, '0')) + 1
                self.router.store.set_meta(counter_key, str(number))
                argument = '' if command == '/earlier' else str(number)
                self.router.store.set_meta('weixin-control:' + chat + ':' + command + ':' + argument, callback)
                retry_command = f"{command}{' ' + argument if argument else ''}"
                label = '重新发送 ' + button['text'] if command == '/file' else button['text']
                controls.append(f"- {retry_command}　{label}")
                if command == '/file':
                    state = self.router.store.get_chat_state(chat)
                    delivery = self.router.store.get_attachment_delivery(chat_id=chat, telegram_user_id=state['telegram_user_id'],
                        portal_actor=state['portal_actor'], token=callback[4:])
                    if delivery:
                        attachments.append({'attachment_token': callback[4:], 'retry_command': retry_command})
        if controls:
            # The button replacements form one list after the text, apart from it.
            text = text.rstrip() + '\n\n' + '\n'.join(controls)
        item['text'] = text
        return item, attachments

    def respond(self, actor, response):
        account = self.store.account(actor)
        if not account:
            return
        chat = chat_key(account['ilink_bot_id'])
        items = list(response.get('messages') or [])
        if response.get('edit'):
            items.append(response['edit'])  # Weixin has no edit operation.
        if response.get('attachment_delivery'):
            # Persist only the authorized delivery reference, never attachment bytes.
            items.append({'attachment_token': response['attachment_delivery']['token']})
        self.enqueue(chat, items)
        self.flush(actor)
        self._reopen_pending(chat)

    def enqueue(self, chat, items):
        for item in items:
            item, attachments = self._render_controls(chat, item)
            self.store.enqueue(chat, item)
            for attachment in attachments:
                self.store.enqueue(chat, attachment)

    def flush(self, actor, *, manual=False, one=False, reminders=True):
        account = self.store.account(actor)
        if not account or account['status'] != 'active':
            return
        chat = chat_key(account['ilink_bot_id'])
        client = self.client_factory(account)
        rows = self.store.pending(chat, manual=manual)
        for row in rows:
            if row['item'].get('notification_actor'):
                if reminders:
                    self._send_reminder(account, row, client)
                continue
            count, blocked = self.store.budget(chat)
            if blocked or not account['context_token']:
                return
            if count >= self.budget - 1:
                if count < self.budget:
                    try:
                        remaining = sum(not row['item'].get('notification_actor') for row in self.store.pending(chat, manual=manual))
                        client.send([{'type': 1, 'text_item': {'text': f'还有 {remaining} 条回复，回复任意消息继续接收'}}],
                                    account['context_token'], client_id=f'telepus-notice-{row["id"]}')
                        self.store.budget(chat, spent=True)
                    except WeixinError as exc:
                        if exc.code == -2:
                            self.store.budget(chat, blocked=True)
                        self._failure(actor, exc)
                return
            item = row['item']
            attachment = None
            try:
                if item.get('attachment_token'):
                    state = self.router.store.get_chat_state(chat)
                    response = self.router._attachment(state, item['attachment_token'])
                    attachment = response.get('attachment_delivery')
                    if not attachment:
                        parts = [{'type': 1, 'text_item': {'text': '\n'.join(x['text'] for x in response['messages'])}}]
                    else:
                        try:
                            parts = [client.upload(attachment['data'], attachment['filename'], attachment['content_type'])]
                        except WeixinError as exc:
                            if exc.code == -14:
                                raise
                            self.router.store.mark_attachment_failed(item['attachment_token'], str(exc))
                            attachment = None
                            retry = item.get('retry_command')
                            hint = f'请发送 {retry} 重试。' if retry else '请重新发送刚才的 /file 命令重试。'
                            parts = [{'type': 1, 'text_item': {'text': '文件发送失败，' + hint}}]
                    segments = []
                else:
                    segments = item.get('segments') or split_text(item.get('text') or '（无文字）')
                    total = int(item.get('segment_total') or len(segments))
                    parts = [{'type': 1, 'text_item': {'text': segment_text(segments, total)}}]
                result = client.send(parts, account['context_token'], client_id=f'telepus-{row["id"]}-{item.get("part", 0)}')
                self.store.budget(chat, spent=True)
                if item.get('question_session'):
                    self.notifications.card_sent(actor, item.get('question_source') or item.get('link') or {})
                if item.get('delivery') or item.get('input'):
                    self.router.waiting.replied(chat)
                server_id = result.get('message_id')
                if server_id:
                    local = self.store.local_id(chat, server_id)
                    link = item.get('link') or {}
                    shown = '\n'.join(p.get('text_item', {}).get('text', '') for p in parts)
                    if link:
                        self.router.store.link_message(chat, local, link['resident_id'], link['message_id'], excerpt=shown)
                    else:
                        # Local replies (commands, receipts) have no Portal source.
                        self.quotes.remember(chat, local, shown)
                    if item.get('question_session'):
                        self.router.store.update_question_session(item['question_session'], telegram_message_id=local)
                if item.get('attachment_token') and attachment:
                    self.router.store.mark_attachment_sent(item['attachment_token'], str(server_id or ''))
                if len(segments) > 1:
                    item.update(segments=segments[1:], part=item.get('part', 0) + 1, segment_total=total)
                    with self.router.store.connect() as c:
                        c.execute('UPDATE outbound_queue SET item=?,manual=TRUE WHERE id=?', (document(item), row['id']))
                else:
                    self.store.finish(row['id'])
                if item.get('delivery'):
                    self.router.store.claim_delivery(chat, item['delivery'])
                if item.get('input'):
                    self.router.store.close_input(item['input'])
                self.store.update(actor, last_error='')
            except WeixinError as exc:
                self._failure(actor, exc)
                if exc.code == -2:
                    self.store.budget(chat, blocked=True)
                return
            if one:
                return

    def _send_reminder(self, account, row, client):
        actor = account['portal_actor']
        chat = chat_key(account['ilink_bot_id'])
        count, blocked = self.store.budget(chat)
        if blocked or count >= self.budget or not account['context_token']:
            return
        item = row['item']
        if time.time() < item.get('retry_at', 0):
            return
        if count == self.budget - 1:
            self._pause_notice(account, row, client)
            return
        try:
            prepared = self.notifications.prepare(actor, self.budget - 1 - count)
        except Exception as exc:
            if not item.get('failures'):
                self.notifications.diagnostic(actor, exc)
            self._retry_reminder(row, item)
            return
        if prepared is None:
            return
        if not prepared:
            self.store.finish(row['id'])
            return
        try:
            for group in prepared:
                identity = hashlib.sha256('\n'.join(group['keys']).encode()).hexdigest()[:24]
                result = client.send([{'type': 1, 'text_item': {'text': group['text']}}], account['context_token'],
                                     client_id=f'telepus-reminder-{identity}')
                self.store.budget(chat, spent=True)
                self.notifications.sent(group, result.get('message_id') or result.get('msg_id'))
            self.store.finish(row['id'])
            if self.notifications.pending(actor):
                self.store.enqueue(chat, {'notification_actor': actor})
        except Exception as exc:
            self._failure(actor, exc)
            if isinstance(exc, WeixinError) and exc.code == -2:
                self.store.budget(chat, blocked=True)
            else:
                self._retry_reminder(row, item)

    PAUSE_NOTICE = '⏸ 微信提醒已暂停：机器人连续发送已达上限。回复任意消息即可继续接收，期间的提醒会合并补发。'

    def _pause_notice(self, account, row, client):
        """Spend the last slot of this inbound on one pause notice.

        Replies and reminders share that slot: whichever notice is sent first
        exhausts the budget, so a second notice cannot follow before the next
        inbound resets it. The reminder row stays queued for that reset.
        """
        actor = account['portal_actor']
        chat = chat_key(account['ilink_bot_id'])
        item = row['item']
        try:
            ready = self.notifications.has_ready(actor)
        except Exception as exc:
            if not item.get('failures'):
                self.notifications.diagnostic(actor, exc)
            self._retry_reminder(row, item)
            return
        if not ready:
            if not self.notifications.pending(actor):
                self.store.finish(row['id'])
            return
        try:
            client.send([{'type': 1, 'text_item': {'text': self.PAUSE_NOTICE}}], account['context_token'],
                        client_id=f'telepus-pause-{row["id"]}')
        except Exception as exc:
            self._failure(actor, exc)
            if isinstance(exc, WeixinError) and exc.code == -2:
                self.store.budget(chat, blocked=True)
            else:
                self._retry_reminder(row, item)
            return
        self.store.budget(chat, spent=True)

    def _retry_reminder(self, row, item):
        item['failures'] = min(item.get('failures', 0) + 1, 9)
        item['retry_at'] = time.time() + min(300, 2 ** item['failures'])
        with self.router.store.connect() as c:
            c.execute('UPDATE outbound_queue SET item=? WHERE id=?', (document(item), row['id']))

    def refresh(self, actor):
        with self.store.lock(actor):
            account = self.store.account(actor)
            if not account or account['status'] != 'active':
                return
            chat = chat_key(account['ilink_bot_id'])
            try:
                self.flush(actor, reminders=False)
                if not any(not row['item'].get('notification_actor') for row in self.store.pending(chat)):
                    state = self.router.store.get_chat_state(chat)
                    outgoing = self.router.refresh_chat(state)
                    self.enqueue(chat, outgoing)
                    self.flush(actor)
                    # Pending queue owns recovery; release the optimistic delivery
                    # claim and reopen inputs while the budget prevents sending.
                    self._reopen_pending(chat)
                self.flush(actor)
                self.router.waiting.send_if_due(chat, lambda text, generation: self._waiting_notice(actor, text, generation))
                self.typing(actor)
            except Exception as exc:
                self._failure(actor, exc)

    def _waiting_notice(self, actor, text, generation):
        account = self.store.account(actor)
        chat = chat_key(account['ilink_bot_id'])
        count, blocked = self.store.budget(chat)
        if blocked or count >= self.budget - 1 or not account['context_token'] or self.store.pending(chat):
            return False
        notice_id = hashlib.sha256(f'{chat}:{generation}'.encode()).hexdigest()[:32]
        try:
            self.client_factory(account).send([{'type': 1, 'text_item': {'text': text}}], account['context_token'],
                                             client_id=f'telepus-wait-{notice_id}')
        except WeixinError as exc:
            if exc.code == -2:
                self.store.budget(chat, blocked=True)
            self._failure(actor, exc)
            return False
        self.store.budget(chat, spent=True)
        return True

    def typing(self, actor):
        account = self.store.account(actor)
        if not account or account['status'] != 'active' or not account['context_token']:
            return
        try:
            client = self.client_factory(account)
            ticket = account['typing_ticket']
            if not ticket:
                ticket = client.request('getconfig', {'ilink_user_id': account['ilink_user_id'],
                    'context_token': account['context_token']}).get('typing_ticket') or ''
                self.store.update(actor, typing_ticket=ticket)
            client.request('sendtyping', {'ilink_user_id': account['ilink_user_id'], 'typing_ticket': ticket,
                'status': 1 if self.router.store.open_inputs(chat_key(account['ilink_bot_id'])) else 2})
        except WeixinError as exc:
            self._failure(actor, exc)

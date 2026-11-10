"""Self-service QR binding, shared by Web ISA and the Telepus Watchboard."""
from __future__ import annotations

import base64
import io
import re
import secrets

from .router_store import RouterStore
from .weixin_client import API, WeixinClient, WeixinError, trusted_url
from .weixin_store import WeixinStore


class WeixinBinding:
    def __init__(self, store=None, client_factory=WeixinClient):
        self.store = WeixinStore(store or RouterStore())
        self.client_factory = client_factory

    @staticmethod
    def user(actor):
        from management_common.principal import actor_principal
        from management_portal.watchboards.telepus import _contact_user
        principal = actor_principal(actor)
        if principal.kind != 'user':
            raise ValueError('微信绑定需要认证 User 身份')
        return _contact_user(principal.id)

    @staticmethod
    def public(account):
        return {'status': account['status'], 'last_error': account['last_error']} if account else {'status': 'unbound'}

    def _new_code(self, actor, session, attempt):
        result = self.client_factory().request('get_bot_qrcode', {'local_token_list': []}, query={'bot_type': 3}, auth=False, timeout=5)
        data = {'qrcode': result['qrcode'], 'content': result['qrcode_img_content'],
                'attempt': attempt, 'baseurl': API, 'status': 'wait'}
        self.store.save_binding(actor, session, data)
        return self._view(session, data)

    @staticmethod
    def _view(session, data):
        import qrcode
        import qrcode.image.svg
        buffer = io.BytesIO()
        qrcode.make(data['content'], image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
        return {'session_id': session, 'status': data['status'], 'attempt': data['attempt'],
                'qr_image': 'data:image/svg+xml;base64,' + base64.b64encode(buffer.getvalue()).decode()}

    def start(self, actor):
        with self.store.lock(actor):
            return self._new_code(actor, secrets.token_hex(16), 1)

    def get(self, actor, session='', pin=''):
        with self.store.lock(actor):
            if not session:
                return self.public(self.store.account(actor))
            data = self.store.binding(actor, session)
            if not data:
                account = self.store.account(actor)
                return self.public(account) if account else {'status': 'expired'}
            query = {'qrcode': data['qrcode']}
            if pin:
                if not re.fullmatch(r'[0-9]{1,12}', pin):
                    raise ValueError('请输入手机上显示的数字')
                query['verify_code'] = pin
            try:
                # QR status is a long poll upstream, but this call runs inside a
                # Web request. Return before the proxy deadline and let the UI
                # poll again; a quiet scan screen is not a failed binding.
                result = self.client_factory().request('get_qrcode_status', query=query, auth=False, base=data['baseurl'], timeout=5)
            except WeixinError as exc:
                if exc.code != 'network':
                    raise
                return {**self._view(session, data), 'retryable': True}
            status = str(result.get('status') or 'wait')
            data['status'] = status
            if status in {'expired', 'verify_code_blocked'}:
                if data['attempt'] >= 3:
                    self.store.delete_binding(actor)
                    return {'status': 'expired'}
                return self._new_code(actor, session, data['attempt'] + 1)
            if status == 'scaned_but_redirect':
                data['baseurl'] = trusted_url('https://' + result['redirect_host'])
            if status == 'confirmed':
                if not all(result.get(k) for k in ('bot_token', 'ilink_bot_id', 'ilink_user_id', 'baseurl')):
                    raise WeixinError('incomplete_binding', 'get_qrcode_status')
                result['baseurl'] = trusted_url(result['baseurl'])
                previous = self.store.account(actor)
                if previous:
                    self._stop(previous)
                    if previous['ilink_bot_id'] != result['ilink_bot_id']:
                        # Preserve pending work across replacement; identity remains this User.
                        self._move_chat(previous['ilink_bot_id'], result['ilink_bot_id'])
                self.store.save_account(actor, result)
                self.store.delete_binding(actor)
                return {'status': 'active'}
            if status == 'binded_redirect':
                self.store.delete_binding(actor)
                return self.public(self.store.account(actor))
            self.store.save_binding(actor, session, data)
            return self._view(session, data)

    def _move_chat(self, old, new):
        from .weixin_store import chat_key
        with self.store.router.connect() as c:
            for table in ('chat_state', 'telegram_inputs', 'delivered_messages', 'message_links', 'question_sessions',
                          'callback_tokens', 'attachment_deliveries', 'outbound_queue'):
                c.execute(f'UPDATE {table} SET chat_id=? WHERE chat_id=?', (chat_key(new), chat_key(old)))
            for prefix in ('weixin-control:', 'weixin-counter:'):
                old_prefix = prefix + chat_key(old) + ':'
                for row in c.execute('SELECT key,value FROM meta WHERE starts_with(key, ?)', (old_prefix,)).fetchall():
                    key = prefix + chat_key(new) + ':' + row['key'][len(old_prefix):]
                    c.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (key, row['value']))

    def _stop(self, account):
        try:
            self.client_factory(account).request('msg/notifystop', timeout=5)
        except WeixinError:
            pass  # Remote expiry/outage must not prevent revoking local access.

    def unbind(self, actor):
        with self.store.lock(actor):
            account = self.store.account(actor)
            if account:
                self._stop(account)
            with self.store.router.connect() as c:
                c.execute('DELETE FROM weixin_accounts WHERE portal_actor=?', (actor,))
            self.store.delete_binding(actor)
        return {'status': 'unbound'}

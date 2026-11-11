"""Latency boundaries with isolated snapshots and a loopback-only HTTP peer."""
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from management_portal.hatg.contracts import InstructionError
from management_portal.telepus.weixin_http import WeixinHTTP
from tests.cases.test_telepus_notifications import reminder, mention, enable  # noqa: F401
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, telepus_schema  # noqa: F401


@pytest.mark.parametrize('delay,reused', [(0, True), (1.9, True), (2.0, False), (3, False)])
def test_only_discovery_callback_reuses_complete_snapshot_for_two_seconds(reminder, delay, reused):
    r = reminder
    enable(r, mention=True)
    r.box['items'] = [mention('new')]
    r.calls.clear()
    def send(actor):
        r.now[0] += delay
        r.adapter.flush(actor)
    r.notices.scan(ACTOR, on_pending=send)
    assert r.calls.count('GetMailbox') == (1 if reused else 2)
    assert len(r.server.sent) == 1
    assert not r.notices._snapshots
    with r.notices.router.connect() as c:
        row = c.execute("SELECT payload_json FROM audit_events WHERE event_type='notification_latency' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row['payload_json']['snapshot_reused'] is reused


def test_expired_snapshot_reread_can_cancel_or_fail_without_sending(reminder):
    r = reminder
    enable(r, mention=True)
    r.box['items'] = [mention('new')]
    def send(actor):
        r.now[0] += 2
        r.box['items'] = []
        r.adapter.flush(actor)
    r.notices.scan(ACTOR, on_pending=send)
    assert not r.server.sent and not r.notices.pending(ACTOR)


def test_complete_snapshot_age_is_measured_from_return_and_read_does_not_hold_send_lock(reminder, monkeypatch):
    r = reminder
    enable(r, mention=True)
    original = r.portal.call
    from contextlib import contextmanager
    held = [False]
    lock = r.notices.store.lock
    @contextmanager
    def tracked_lock(actor):
        with lock(actor):
            held[0] = True
            try:
                yield
            finally:
                held[0] = False
    monkeypatch.setattr(r.notices.store, 'lock', tracked_lock)
    def call(actor, instruction, arguments):
        if instruction == 'GetMailbox':
            assert not held[0]
            r.now[0] += 2.1
        return original(actor, instruction, arguments)
    monkeypatch.setattr(r.portal, 'call', call)
    r.box['items'] = [mention('new')]
    r.calls.clear()
    r.notices.scan(ACTOR, on_pending=r.adapter.flush)
    assert r.calls.count('GetMailbox') == 1


def test_restart_owner_unavailable_is_quiet_but_persistent_and_permission_errors_are_visible(reminder, monkeypatch, capsys):
    r = reminder
    enable(r, mention=True)
    code = ['instruction.owner_unavailable']
    def watch(*args):
        raise InstructionError(code[0], 'PRIVATE upstream text', details={'owner': 'web'}, retryable=True)
    monkeypatch.setattr(r.portal, 'first_batch', watch)
    for index in range(4):
        delay = r.notices.scan(ACTOR)
        assert delay == 2 ** (index + 1)
        output = capsys.readouterr().err
        assert bool(output) == (index == 3)
        assert 'PRIVATE' not in output
        r.now[0] += delay
    with r.notices.router.connect() as c:
        c.execute("DELETE FROM meta WHERE key=?", ('notification-watch:' + ACTOR,))
    r.notices.scan(ACTOR)
    code[0] = 'instruction.forbidden'
    r.notices.scan(ACTOR)
    assert 'instruction.forbidden' in capsys.readouterr().err


def test_http_keepalive_reuses_connection_without_reusing_auth_or_replaying_posts(monkeypatch):
    from management_portal.telepus import weixin_http
    seen = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_POST(self):
            seen.append((self.client_address, self.headers.get('Authorization'), self.rfile.read(int(self.headers['Content-Length']))))
            if self.path == '/drop':
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def connect(host, port, timeout):
        return HTTPConnection('127.0.0.1', server.server_port, timeout=timeout)
    monkeypatch.setattr(weixin_http, 'HTTPSConnection', connect)
    monkeypatch.setattr(weixin_http, 'getproxies', lambda: {})
    http = WeixinHTTP()
    def request(path, auth):
        return http.request('https://ilinkai.weixin.qq.com' + path, 'POST', {'Authorization': auth}, b'{}', 2,
                            limit=100, fallback=lambda *args: pytest.fail('unexpected proxy fallback'))
    try:
        request('/send', 'user-A')
        assert not http.local.timing['connection_reused']
        request('/send', 'user-B')
        assert http.local.timing['connection_reused']
        assert seen[0][0] == seen[1][0] and [s[1] for s in seen] == ['user-A', 'user-B']
        with pytest.raises(Exception):
            request('/drop', 'user-B')
        assert len(seen) == 3 and not http.local.pool
        request('/send', 'user-C')
        assert len(seen) == 4 and not http.local.timing['connection_reused']
    finally:
        for connection in http.local.pool.values():
            connection.close()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_web_watch_waiter_returns_immediately_on_local_notification(monkeypatch):
    from threading import Event
    from types import SimpleNamespace
    from management_portal import notifications, web_isa
    from management_portal.notifications.bus import NotificationHub
    from management_portal.hatg.socket_rpc import _decode
    hub = NotificationHub()
    monkeypatch.setattr(notifications, 'get_notification_hub', lambda: hub)
    monkeypatch.setattr(web_isa, '_named_user', lambda inv: 'isolated-user')
    monkeypatch.setattr(web_isa, 'web', lambda module: SimpleNamespace(_portal_change_payload=lambda *args:
        {'projection_changes': {'agent-conversation-index': ['isolated-user']}}))
    invocation = SimpleNamespace(argument=lambda key: {'timeout_seconds': 2}.get(key))
    stream = web_isa.watch_inbox(invocation)
    assert next(stream)[0]['snapshot_required']
    received, ready = Event(), Event()
    duration = []
    def waiting():
        ready.set()
        assert next(stream)[0]['snapshot_required']
        duration.append(time.monotonic())
        received.set()
    thread = Thread(target=waiting, daemon=True)
    thread.start()
    assert ready.wait(1)
    started = time.monotonic()
    hub.publish('projection.changed', {})
    assert received.wait(1)
    assert duration[0] - started < .5
    thread.join(1)
    stream.close()
    # A Web restart closes the existing streaming socket: the real decoder
    # turns EOF into exactly the transient code the watcher handles quietly.
    with pytest.raises(InstructionError) as error:
        _decode('web', b'', 'isolated-request')
    assert error.value.code == 'instruction.owner_unavailable' and error.value.retryable

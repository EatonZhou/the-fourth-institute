"""Telepus schedulers retain only live work and enumerate identities without credentials.

:design: decision_c41e1310c463
"""
from concurrent.futures import Future
from contextlib import contextmanager
import gc
from types import SimpleNamespace
import weakref

import pytest

from management_portal.telepus import cli, refresh_service, weixin_adapter
from management_portal.telepus.weixin_store import WeixinStore


@pytest.mark.parametrize('interrupt', [False, True])
def test_refresh_releases_completed_inactive_accounts_and_closes_on_exit(monkeypatch, interrupt):
    import concurrent.futures
    refs, submitted, closed = {}, [], []
    tick = [0]

    class Pool:
        def __init__(self, **kwargs):
            pass

        def submit(self, function, actor):
            submitted.append(actor)
            task = Future()
            refs[actor] = weakref.ref(task)
            return task

        def shutdown(self, **kwargs):
            closed.append(kwargs)

    def sleep(_):
        tick[0] += 1
        if tick[0] == 1:
            refs['gone']().set_exception(ValueError('completed failure'))
        else:
            gc.collect()
            assert refs['gone']() is None, 'completed work from an unbound actor must be collectible'
            assert refs['slow']() is not None, 'inflight work still prevents duplicate submission'
            if interrupt:
                raise KeyboardInterrupt

    def actors():
        return ['gone', 'slow'] if tick[0] == 0 else ['slow']

    monkeypatch.setattr(concurrent.futures, 'ThreadPoolExecutor', Pool)
    monkeypatch.setattr(cli, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=sleep))
    monkeypatch.setattr(cli, 'TelegramClient', lambda: object())
    monkeypatch.setattr(cli, 'telegram_adapter', lambda _: SimpleNamespace(
        router=SimpleNamespace(store=SimpleNamespace(prune_expired_callback_tokens=lambda: None)),
        refresh_routes=lambda **kw: {}))
    monkeypatch.setattr(weixin_adapter, 'WeixinAdapter', lambda: SimpleNamespace(
        store=SimpleNamespace(active_actors=actors), refresh=lambda actor: None))
    monkeypatch.setattr(refresh_service, 'NotificationScheduler', lambda _: SimpleNamespace(
        start=lambda: None, close=lambda: closed.append('notifications')))
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            cli.run_telegram_refresh_daemon(['--max-iterations', '3'])
    else:
        assert cli.run_telegram_refresh_daemon(['--max-iterations', '3']) == 0
    assert submitted == ['gone', 'slow']
    assert closed == ['notifications', {'wait': False, 'cancel_futures': True}]
    gc.collect()
    assert all(ref() is None for ref in refs.values())


def test_scheduler_identity_query_never_selects_credential_columns():
    calls = []

    class Connection:
        def execute(self, sql):
            calls.append(sql)
            return SimpleNamespace(fetchall=lambda: [{'portal_actor': 'user_a'}])

    @contextmanager
    def connect():
        yield Connection()

    store = WeixinStore(SimpleNamespace(init_schema=lambda: None, connect=connect))
    assert store.active_actors() == ['user_a']
    select, where = calls[0].lower().split('where')
    assert select.strip() == 'select portal_actor from weixin_accounts'
    assert "status='active'" in where


@pytest.mark.parametrize('state', ['inactive', 'rotated', 'normal'])
def test_intake_worker_reads_binding_and_keeps_token_generation(monkeypatch, state):
    from management_portal.telepus import weixin_service
    calls, polls = [], []

    class Stop:
        value = False
        def is_set(self):
            return self.value
        def set(self):
            self.value = True
        def wait(self, seconds):
            self.set()

    stopped = Stop()

    class Worker:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
        def is_alive(self):
            return False
        def join(self, **kwargs):
            pass

    first = {'bot_token': 'old', 'status': 'inactive' if state == 'inactive' else 'active'}
    second = {'bot_token': 'new' if state == 'rotated' else 'old', 'status': 'active'}
    accounts = iter([first, second])
    local = SimpleNamespace(store=SimpleNamespace(account=lambda actor: next(accounts)),
        client_factory=lambda account: SimpleNamespace(request=lambda route: calls.append((account['bot_token'], route))),
        poll_once=lambda actor: (polls.append(actor), stopped.set()))
    adapters = iter([SimpleNamespace(store=SimpleNamespace(active_actors=lambda: ['user_a'])), local])
    monkeypatch.setattr(weixin_service, 'WeixinAdapter', lambda: next(adapters))
    monkeypatch.setattr(weixin_service, 'threading', SimpleNamespace(Event=lambda: stopped, Thread=Worker))
    monkeypatch.setattr(weixin_service.signal, 'signal', lambda *args: None)
    assert weixin_service.main() == 0
    if state == 'inactive':
        assert calls == polls == []
    elif state == 'rotated':
        assert calls == [('old', 'msg/notifystart')]
        assert polls == []
    else:
        assert calls == [('old', 'msg/notifystart'), ('old', 'msg/notifystop')]
        assert polls == ['user_a']

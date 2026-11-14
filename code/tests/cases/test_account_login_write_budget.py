"""Login fact replay and ordering against isolated PostgreSQL, without a Provider.

:design: decision_7956d7cb4bde
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest

from management_portal.execution import account_login as login


STAMP = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture
def measured_store():
    store = login.LoginSessionStore()
    with store.connection() as connection:
        connection.execute('CREATE TABLE login_write_updates(account_id TEXT NOT NULL)')
        connection.execute('''CREATE FUNCTION count_login_update() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN INSERT INTO login_write_updates VALUES (NEW.account_id); RETURN NEW; END $$''')
        connection.execute('''CREATE TRIGGER count_login_update AFTER UPDATE ON login_states
            FOR EACH ROW EXECUTE FUNCTION count_login_update()''')
    try:
        yield store
    finally:
        # The shared isolated test schema is truncated, not dropped, between
        # cases. Retire this test-only instrumentation after each invocation.
        with store.connection() as connection:
            connection.execute('DROP TRIGGER count_login_update ON login_states')
            connection.execute('DROP FUNCTION count_login_update()')
            connection.execute('DROP TABLE login_write_updates')


def fact(store, account_id='synthetic'):
    with store.connection() as connection:
        row = connection.execute('SELECT state,observed_at,xmin::text AS xmin FROM login_states '
                                 'WHERE account_id=%s', (account_id,)).fetchone()
        updates = connection.execute('SELECT count(*) AS n FROM login_write_updates WHERE account_id=%s',
                                     (account_id,)).fetchone()['n']
    return (dict(row) if row else None), updates


@pytest.mark.parametrize('state', ['logged_in', 'login_expired', 'logged_out'])
def test_identical_fact_replay_keeps_xmin_and_emits_no_update(measured_store, state, record_property):
    store = measured_store
    store.observe('synthetic', state, observed_at=STAMP)
    before, _ = fact(store)
    for _ in range(4):
        store.observe('synthetic', state, observed_at=STAMP)
    after, updates = fact(store)
    counts = {'replay_calls': 4, 'row_updates': updates,
              'xmin_changed': int(before['xmin'] != after['xmin'])}
    for key, value in counts.items():
        record_property(key, value)
    print(state, counts)
    assert updates == 0
    assert after == before


def test_equal_instant_different_timezone_is_the_same_fact(measured_store):
    store = measured_store
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    before, _ = fact(store)
    store.observe('synthetic', 'logged_in', observed_at=STAMP.astimezone(timezone(timedelta(hours=8))))
    assert fact(store) == (before, 0)


def test_same_time_changes_new_time_and_late_login_keep_ordering(measured_store):
    store = measured_store
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    before, _ = fact(store)
    store.observe('synthetic', 'logged_out', observed_at=STAMP)
    changed, updates = fact(store)
    assert changed['state'] == 'logged_out' and changed['xmin'] != before['xmin'] and updates == 1
    store.observe('synthetic', 'login_expired', observed_at=STAMP)
    changed, updates = fact(store)
    assert changed['state'] == 'login_expired' and updates == 2
    later = STAMP + timedelta(seconds=1)
    store.observe('synthetic', 'login_expired', observed_at=later)
    advanced, updates = fact(store)
    assert advanced['observed_at'] == later and advanced['xmin'] != changed['xmin'] and updates == 3
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    assert fact(store) == (advanced, 3)


@pytest.mark.parametrize('late_state,late_stamp', [('logged_in', STAMP), ('login_expired', STAMP + timedelta(seconds=1))])
def test_waiting_writer_checks_committed_fact(measured_store, late_state, late_stamp):
    store = measured_store
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    started, pid = Event(), []

    def delayed():
        with store.connection() as connection:
            connection.execute("SET LOCAL statement_timeout='5s'")
            pid.append(connection.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'])
            started.set()
            store._observe(connection, 'synthetic', late_state, late_stamp)

    with ThreadPoolExecutor(max_workers=1) as executor:
        with store.connection() as connection:
            store._observe(connection, 'synthetic', 'login_expired', STAMP + timedelta(seconds=1))
            future = executor.submit(delayed)
            assert started.wait(3)
            deadline = time.monotonic() + 3
            while True:
                blocked = connection.execute('SELECT pg_blocking_pids(%s) AS pids', (pid[0],)).fetchone()['pids']
                if blocked:
                    break
                assert time.monotonic() < deadline, 'writer did not reach the row conflict'
                time.sleep(0.01)
            assert not future.done()
        future.result(timeout=5)
    current, updates = fact(store)
    assert current['state'] == 'login_expired'
    assert current['observed_at'] == STAMP + timedelta(seconds=1)
    assert updates == 1


def test_deleted_fact_can_be_rebuilt_from_same_source(measured_store):
    store = measured_store
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    before, _ = fact(store)
    store.remove('synthetic')
    assert fact(store) == (None, 0)
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    rebuilt, updates = fact(store)
    assert rebuilt['state'] == before['state'] and rebuilt['observed_at'] == before['observed_at']
    assert rebuilt['xmin'] != before['xmin'] and updates == 0


def test_login_success_commits_when_fact_already_matches(measured_store, monkeypatch):
    store = measured_store
    monkeypatch.setattr(login, 'now', lambda: STAMP)
    store.observe('synthetic', 'logged_in', observed_at=STAMP)
    before, _ = fact(store)
    session = store.begin('synthetic')
    assert store.transition(session, 'succeeded')
    assert store.get('synthetic')['state'] == 'succeeded'
    assert fact(store) == (before, 0)


def test_passive_replays_keep_every_history_attempt(measured_store, record_property):
    from management_portal.execution.account_monitor import AccountMonitor
    from management_portal.execution.account_observation_store import AccountObservationStore

    store, history = measured_store, AccountObservationStore()
    account = SimpleNamespace(account_id='synthetic', provider='claude', kind='subscription_login',
                              billing_account_id='', quota_policy='reserve', quota_enabled=False)
    accounts = Mock(adapters={'claude': object()})
    accounts.accounts.return_value = (account,)
    accounts.observe.side_effect = lambda *a, **kw: {
        'status': 'ok', 'login_observation': 'logged_in', 'login_observed_at': STAMP.isoformat()}
    accounts.runtime_catalog.return_value = {'models': []}
    monitor = AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=store,
                             observation_store=history)
    for _ in range(4):
        monitor.refresh_account(account, passive=True)
    with history.connection() as connection:
        attempts = connection.execute('SELECT count(*) AS n FROM observations WHERE account_id=%s',
                                      ('synthetic',)).fetchone()['n']
    current, updates = fact(store)
    assert current['state'] == 'logged_in'
    counts = {'fake_provider_calls': accounts.observe.call_count, 'history_attempts': attempts,
              'row_updates': updates}
    for key, value in counts.items():
        record_property(key, value)
    print(counts)
    assert attempts == accounts.observe.call_count == 4
    assert updates == 0


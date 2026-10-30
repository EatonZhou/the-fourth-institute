"""Only a disconnected, pinned service SQL wait may receive a cancel signal."""
import copy
import socket
import struct
from unittest.mock import Mock
import pytest
from scripts import tfi_application_clients as clients


class Processes:
    def __init__(self):
        self.stamps = {10: 100, 11: 200, 12: 300, 20: 400}
        self.tree = {12: 11, 11: 10, 10: 1}
        self.dead = set()
        self.rows = [('127.0.0.1', 43210, '127.0.0.1', 5432, 12, 5)]
    def pin(self, pid):
        assert pid not in self.dead
        return self.stamps[pid]
    def exited(self, pid):
        return pid in self.dead
    def parents(self):
        return self.tree
    def tcp(self):
        return self.rows


def row():
    return dict(pid=20, backend_start='2026-10-30 12:00:00+00', datid=1, usesysid=2,
        client_addr='127.0.0.1', client_port=43210, state='active',
        wait_event_type='Lock', wait_event='relation', blockers=[30],
        query_start='2026-10-30 12:01:00+00', xact_start='2026-10-30 12:01:00+00')


def captured(monkeypatch):
    processes = Processes()
    current = [row()]
    monkeypatch.setattr(clients, 'clients', lambda _: copy.deepcopy(current))
    monkeypatch.setattr(clients, 'lock_requests', lambda *_: ['1:42:RowExclusiveLock'])
    monkeypatch.setattr(clients, 'assert_no_foreign_clients', lambda *_: None)
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = ('127.0.0.1', 5432, 30)
    evidence = clients.capture(connection, processes, {11: (200, 10, 100)})
    connection.reset_mock()
    processes.dead = {10, 11, 12}
    processes.rows = []
    connection.execute.return_value.fetchall.return_value = [(True,)]
    return connection, processes, current, evidence


def test_native_tcp_format_preserves_full_endpoint_and_owner():
    address = int.from_bytes(socket.inet_aton('127.0.0.1'), 'little')
    raw = struct.pack('<7I', 1, 5, address, socket.htons(43210), address, socket.htons(5432), 12)
    assert clients.decode_tcp(raw) == Processes().rows
    with pytest.raises(ValueError, match='Incomplete'):
        clients.decode_tcp(raw[:-1])
    address6 = socket.inet_pton(socket.AF_INET6, '::ffff:127.0.0.1')
    raw6 = struct.pack('<I16sII16sIIII', 1, address6, 0, socket.htons(43210),
                       address6, 0, socket.htons(5432), 12, 12)
    assert clients.decode_tcp(raw6, socket.AF_INET6) == [(*Processes().rows[0][:5], 12)]


def test_only_pinned_disconnected_service_wait_receives_one_signal(monkeypatch):
    connection, processes, current, evidence = captured(monkeypatch)
    assert clients.clear_disconnected(connection, processes, evidence) == 1
    signal = [call for call in connection.execute.call_args_list if 'pg_cancel_backend' in call.args[0]]
    assert len(signal) == 1
    assert signal[0].args[1] == (20, row()['backend_start'], 1, 2, '127.0.0.1', 43210, 30,
        row()['query_start'], row()['xact_start'], ['1:42:RowExclusiveLock'], 30, [20])


@pytest.mark.parametrize('case', ['new', 'generation', 'user', 'endpoint', 'live_client',
    'live_ancestor', 'dead_backend', 'tcp', 'idle', 'other_lock', 'other_blocker', 'additional_blocker',
    'no_query_start', 'no_xact_start', 'not_relation'])
def test_unproven_residual_is_never_signalled(monkeypatch, case):
    connection, processes, current, evidence = captured(monkeypatch)
    if case == 'new': current[0]['pid'] = 21
    elif case == 'generation': current[0]['backend_start'] += '1'
    elif case == 'user': current[0]['usesysid'] = 3
    elif case == 'endpoint': current[0]['client_port'] += 1
    elif case == 'live_client': processes.dead.remove(12)
    elif case == 'live_ancestor': processes.dead.remove(10)
    elif case == 'dead_backend': processes.dead.add(20)
    elif case == 'tcp': processes.rows = Processes().rows
    elif case == 'idle': current[0]['state'] = 'idle'
    elif case == 'other_lock': current[0]['wait_event_type'] = 'IO'
    elif case == 'other_blocker': current[0]['blockers'] = [31]
    elif case == 'additional_blocker': current[0]['blockers'] = [30, 31]
    elif case == 'no_query_start': current[0]['query_start'] = None
    elif case == 'no_xact_start': current[0]['xact_start'] = None
    elif case == 'not_relation': current[0]['wait_event'] = 'transactionid'
    with pytest.raises(RuntimeError, match='not a proven'):
        clients.clear_disconnected(connection, processes, evidence)
    connection.execute.assert_not_called()


def test_disappearance_or_failed_signal_does_not_authorize_snapshot(monkeypatch):
    connection, processes, current, evidence = captured(monkeypatch)
    connection.execute.return_value.fetchall.return_value = []
    with pytest.raises(RuntimeError, match='changed before'):
        clients.clear_disconnected(connection, processes, evidence)


@pytest.mark.parametrize('case', ['unknown', 'recycled_parent', 'recycled_root', 'wrong_supervisor'])
def test_ancestry_refuses_unproven_service_ownership(case):
    processes = Processes()
    roots = {11: (200, 10, 100)}
    if case == 'unknown': roots = {}
    elif case == 'recycled_parent': processes.stamps[11] = 400
    elif case == 'recycled_root': roots[11] = (199, 10, 100)
    elif case == 'wrong_supervisor': processes.tree[11] = 999
    with pytest.raises((ValueError, KeyError)):
        clients.ancestry(processes, 12, roots)


def test_unknown_among_known_clients_prevents_all_signals(monkeypatch):
    connection, processes, current, evidence = captured(monkeypatch)
    current.append({**row(), 'pid': 21})
    with pytest.raises(RuntimeError, match='not a proven'):
        clients.clear_disconnected(connection, processes, evidence)
    connection.execute.assert_not_called()


def test_second_instance_unknown_prevents_first_instance_signals(monkeypatch):
    connection, processes, current, evidence = captured(monkeypatch)
    other = Mock()
    monkeypatch.setattr(clients, 'clients', lambda c: copy.deepcopy(current if c is connection else [{**row(), 'pid':21}]))
    with pytest.raises(RuntimeError, match='not a proven'):
        clients.clear_all([connection, other], processes, [evidence, evidence])
    connection.execute.assert_not_called()
    other.execute.assert_not_called()


def test_query_change_after_global_validation_is_not_cancelled(monkeypatch):
    connection, processes, current, evidence = captured(monkeypatch)
    plan = clients.plan_disconnected(connection, processes, evidence)
    current[0]['query_start'] += '1'
    with pytest.raises(RuntimeError, match='all-instance validation'):
        clients.clear_disconnected(connection, processes, evidence, plan)
    connection.execute.assert_not_called()


@pytest.mark.parametrize('rows', [[], [(1, 42, 'RowExclusiveLock', False)],
    [(None, None, 'ExclusiveLock', False)], [(1, 42, 'AccessShareLock', True)]])
def test_unknown_or_nonconflicting_relation_lock_refuses(rows):
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = rows
    with pytest.raises(RuntimeError, match='held application relation barrier'):
        clients.lock_requests(connection, 20, 30)


def capture_fixture(monkeypatch, snapshots):
    processes = Processes()
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = ('127.0.0.1', 5432, 30)
    pending = iter(snapshots)
    last = []
    def read(_):
        nonlocal last
        last = next(pending, last)
        return copy.deepcopy(last)
    monkeypatch.setattr(clients, 'clients', read)
    monkeypatch.setattr(clients, 'assert_no_foreign_clients', lambda *_: None)
    monkeypatch.setattr(clients.time, 'sleep', lambda _: None)
    return connection, processes


def test_departed_socket_requires_actual_backend_disappearance(monkeypatch):
    connection, processes = capture_fixture(monkeypatch, [[row()], []])
    processes.rows = []
    evidence = clients.capture(connection, processes, {11: (200, 10, 100)})
    assert evidence['clients'] == {}
    assert evidence['sampling']['deferred'] == 1
    assert not any('pg_cancel_backend' in c.args[0] for c in connection.execute.call_args_list)


def test_arriving_connection_is_proven_without_recapturing_stable_client(monkeypatch):
    other = {**row(), 'pid': 21, 'client_port': 43211}
    connection, processes = capture_fixture(monkeypatch, [[row()], [row(), other], [row(), other]])
    processes.stamps[21] = 450
    processes.rows.append(('127.0.0.1', 43211, '127.0.0.1', 5432, 12, 5))
    ancestry = clients.ancestry
    seen = []
    def observe(*args):
        seen.append(args[1])
        return ancestry(*args)
    monkeypatch.setattr(clients, 'ancestry', observe)
    evidence = clients.capture(connection, processes, {11: (200, 10, 100)})
    assert set(evidence['clients']) == {20, 21}
    assert evidence['sampling']['rounds'] == 2
    assert seen == [12, 12]  # One proof for each connection, not a global restart.


def test_new_unknown_connection_is_not_hidden_by_capture_churn(monkeypatch):
    other = {**row(), 'pid': 21, 'client_port': 43211}
    connection, processes = capture_fixture(monkeypatch, [[row()], [row(), other], [row(), other]])
    processes.rows.append(('127.0.0.1', 43211, '127.0.0.1', 5432, 99, 5))
    processes.stamps[99] = 350
    with pytest.raises(ValueError, match='not a proven owned'):
        clients.capture(connection, processes, {11: (200, 10, 100)})
    assert not any('pg_cancel_backend' in c.args[0] for c in connection.execute.call_args_list)


@pytest.mark.parametrize('case', ['missing', 'closing', 'continuous_arrival'])
def test_unproven_persistent_or_never_converging_clients_refuse_with_bound(monkeypatch, case):
    connection, processes = capture_fixture(monkeypatch, [[row()]])
    if case == 'missing': processes.rows = []
    if case == 'closing': processes.rows = [(*processes.rows[0][:5], 8)]
    if case == 'continuous_arrival':
        calls = []
        def changing(_):
            calls.append(1)
            pid = 20 + len(calls)
            processes.stamps[pid] = 400
            return [{**row(), 'pid': pid}]
        monkeypatch.setattr(clients, 'clients', changing)
    monkeypatch.setattr(clients.time, 'monotonic', lambda: 0)  # Round cap also bounds a frozen clock.
    with pytest.raises(ValueError, match='bounded capture'):
        clients.capture(connection, processes, {11: (200, 10, 100)})
    if case == 'continuous_arrival': assert len(calls) == 128


@pytest.mark.parametrize('case', ['duplicate', 'permission', 'recycled_parent'])
def test_still_present_ambiguity_or_bad_identity_is_not_retried(monkeypatch, case):
    connection, processes = capture_fixture(monkeypatch, [[row()]])
    if case == 'duplicate': processes.rows.append((*processes.rows[0][:4], 99, 5))
    if case == 'permission':
        def denied(*_): raise PermissionError('denied')
        processes.pin = denied
    if case == 'recycled_parent': processes.stamps[11] = 500
    monkeypatch.setattr(clients.time, 'sleep', lambda _: pytest.fail('must refuse immediately'))
    with pytest.raises((ValueError, PermissionError)):
        clients.capture(connection, processes, {11: (200, 10, 100)})


def test_pin_failure_only_discarded_after_exact_backend_disappears(monkeypatch):
    connection, processes = capture_fixture(monkeypatch, [[row()], [], []])
    def vanished(*_): raise OSError('process exited')
    processes.pin = vanished
    evidence = clients.capture(connection, processes, {11: (200, 10, 100)})
    assert evidence['clients'] == {}
    assert evidence['sampling']['departed'] == 1


def test_tcp_change_cannot_commit_partial_identity_proof(monkeypatch):
    connection, processes = capture_fixture(monkeypatch, [[row()]])
    rows = processes.rows
    samples = iter([rows, [], [], []])
    processes.tcp = lambda: next(samples, [])
    clock = iter([0, 1, 6])
    monkeypatch.setattr(clients.time, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='bounded capture'):
        clients.capture(connection, processes, {11: (200, 10, 100)})


def test_missing_socket_must_later_obtain_full_proof(monkeypatch):
    connection, processes = capture_fixture(monkeypatch, [[row()]])
    calls = iter([[], [], processes.rows, processes.rows])
    processes.tcp = lambda: next(calls)
    evidence = clients.capture(connection, processes, {11: (200, 10, 100)})
    assert evidence['sampling']['rounds'] == 2
    assert evidence['clients'][20]['chain'] == [(12, 300), (11, 200), (10, 100)]


def test_same_pid_changed_identity_never_inherits_prior_ownership(monkeypatch):
    changed = {**row(), 'backend_start': 'another generation', 'client_port': 43211}
    connection, processes = capture_fixture(monkeypatch, [[row()], [changed], [changed]])
    processes.rows.append(('127.0.0.1', 43211, '127.0.0.1', 5432, 99, 5))
    processes.stamps[99] = 350
    with pytest.raises(ValueError, match='not a proven owned'):
        clients.capture(connection, processes, {11: (200, 10, 100)})


def test_foreign_database_client_at_capture_end_still_refuses(monkeypatch):
    connection, processes = capture_fixture(monkeypatch, [[row()]])
    checks = []
    def foreign(_):
        checks.append(1)
        if len(checks) > 1: raise RuntimeError('Other database')
    monkeypatch.setattr(clients, 'assert_no_foreign_clients', foreign)
    with pytest.raises(RuntimeError, match='Other database'):
        clients.capture(connection, processes, {11: (200, 10, 100)})


def test_context_releases_all_pins_when_capture_fails(monkeypatch):
    processes = Mock()
    api = Mock(COMPONENTS=[])
    monkeypatch.setattr(clients.sys, 'platform', 'win32')
    monkeypatch.setattr(clients, 'WindowsProcesses', lambda: processes)
    from scripts import tfi_release_provider_services as providers
    monkeypatch.setattr(providers, 'owned_services', lambda *_: {})
    def failed(*_): raise ValueError('bounded capture')
    monkeypatch.setattr(clients, 'capture', failed)
    with pytest.raises(ValueError, match='bounded capture'):
        with clients.owned_clients(api, [{'target': {'windows_service_mode': 'user'}}], [Mock()]):
            pytest.fail('must not yield partial ownership')
    processes.close.assert_called_once_with()

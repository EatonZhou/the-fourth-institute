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

"""Competing binding replacements cannot both consume the same expected state."""
from concurrent.futures import ThreadPoolExecutor
import threading

from management_portal.execution import agent_data_sessions
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.session_manager import SessionManager


def test_competing_replacements_accept_original_binding_only_once(monkeypatch):
    manager = SessionManager(agent_data=AgentDataStore())
    for binding, native in [('current', 'original'), ('prepared-a', 'next-a'), ('prepared-b', 'next-b')]:
        manager.bind_session(binding, provider='codex', backend_session_id=native, backend_route_id='zero')
    original = manager.provider_binding('current')
    read = agent_data_sessions._binding_row
    first_read, second_attempt, second_read, proceed = (threading.Event() for _ in range(4))
    local = threading.local()

    def observed(connection, binding_id, **kwargs):
        lane = getattr(local, 'lane', '')
        row = read(connection, binding_id, **kwargs)
        if binding_id == 'current' and lane == 'first' and not first_read.is_set():
            first_read.set()
            assert proceed.wait(5)
        if binding_id == 'current' and lane == 'second':
            second_read.set()
        return row

    def replace(lane, prepared):
        local.lane = lane
        if lane == 'second':
            second_attempt.set()
        try:
            return manager.replace_session('current', prepared, expected_binding=original)
        except RuntimeError as exc:
            return exc

    monkeypatch.setattr(agent_data_sessions, '_binding_row', observed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(replace, 'first', 'prepared-a')
        try:
            assert first_read.wait(5)
            second = pool.submit(replace, 'second', 'prepared-b')
            assert second_attempt.wait(5)
            # An unlocked implementation reads/commits B while A is paused.
            # A row-locked implementation leaves B waiting until A completes.
            second_read.wait(0.5)
        finally:
            proceed.set()
        results = [first.result(5), second.result(5)]
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, RuntimeError)]
    assert len(failures) == 1 and 'changed before replacement' in str(failures[0])
    winner = next(result for result in results if isinstance(result, dict))
    assert manager.provider_binding('current') == winner['current_binding']

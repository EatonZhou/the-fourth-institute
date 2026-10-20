"""Dispatch timing is content provenance, independent of message/turn time."""
from dataclasses import replace
from datetime import datetime

import pytest

from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.agent_data_store import AgentDataStore
from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation


@pytest.fixture
def observation_enabled():
    from management_portal.execution.service_settings import ExecutionSettings
    ExecutionSettings().update(True)


def test_direct_output_timing_and_unknown_dispatch_are_distinct(observation_enabled):
    """Provider 直接输出带请求计时时，模型片段的开始与结束时间取自该计时"""
    store = AgentDataStore()
    request = _turn_request('resident_123456789abc')
    session = _runtime_session(request, 'session-direct')
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, _invocation(request, session))
    timing = {'started_at':'2026-10-14T10:00:00+00:00', 'finished_at':'2026-10-14T10:00:08+00:00'}
    projector.observe_runtime_event(request.execution_id, 'provider_output',
                                    {'id':'direct', 'text':'answer', 'final':True, 'request_timing':timing})
    with store.database.read() as connection:
        row = connection.execute("SELECT started_at,finished_at FROM segments WHERE segment_type='model'").fetchone()
    assert tuple(row) == tuple(datetime.fromisoformat(timing[key]) for key in ('started_at','finished_at'))


@pytest.mark.parametrize('provider', ['codex', 'claude', 'kimi', 'dsh', 'opencode'])
@pytest.mark.parametrize('observed', [False, True])
def test_model_timing_roundtrip_does_not_invent_dispatch(provider, observed, observation_enabled):
    """各 Provider 未观测到请求计时时模型片段开始时间留空，不用事件时间代替"""
    store = AgentDataStore()
    request = replace(_turn_request('resident_123456789abc'), provider=provider)
    session = _runtime_session(request, 'session-test')
    projector = AgentDataProjector(store)
    projector.begin(request, session, created_at='2026-10-14T09:00:00Z')
    projector.observe_invocation(request.execution_id, _invocation(request, session))
    timing = {'started_at': '2026-10-14T10:00:00+00:00', 'finished_at': '2026-10-14T10:00:08+00:00'}
    for item in ({'id': 'reason', 'type': 'reasoning', 'content': ['thinking']},
                 {'id': 'answer', 'type': 'agentMessage', 'text': 'answer'}):
        params = {'threadId': session.backend_session_id, 'item': item, 'turnId': 'turn-test',
                  'started_at': '2026-10-14T09:30:00Z'}
        if observed:
            params['request_timing'] = timing
        projector.observe(provider=provider, backend_route_id='zero', backend_session_id=session.backend_session_id,
                          execution_id=request.execution_id, event={'method': 'item/completed', 'params': params})
    with store.database.read() as connection:
        rows = connection.execute("SELECT * FROM segments WHERE segment_type='model' ORDER BY segment_id").fetchall()
    assert len(rows) == 2
    for row in rows:
        assert 'created_at' not in row.keys()
        assert row['started_at'] == (datetime.fromisoformat(timing['started_at']) if observed else None)
        assert row['finished_at'] is not None
        if observed:
            assert row['finished_at'] == datetime.fromisoformat(timing['finished_at'])

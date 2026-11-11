"""Accepted prompts must not hang or be replayed when the local server disappears.

:module: execution.providers
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import kimi_code_client as kimi
from management_portal.execution.provider_continuation import provider_error_continuation


def follow(monkeypatch, tmp_path, *, recover=False, handover=False):
    clock = [0.0]
    monkeypatch.setattr(kimi.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(kimi.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(kimi, 'SNAPSHOT_RECOVERY_SECONDS', 10)
    monkeypatch.setattr('management_portal.kimi_code_server.model_catalog', lambda: {'context_windows': {}})
    client = kimi.KimiCodeClient('http://127.0.0.1:1', home=tmp_path)
    seen = []
    def snapshot(_session):
        seen.append(clock[0])
        if recover and 6 <= clock[0] < 8:
            return {'session': {'busy': True}, 'in_flight_turn': {'turn_id': 'accepted'}}
        raise kimi.KimiCodeUnavailable('connection refused')
    client.get_snapshot = snapshot
    client._request = Mock(side_effect=AssertionError('No prompt or abort may be submitted'))
    stream = SimpleNamespace(drain=lambda _: None)
    error = kimi.KimiCodeDetached if handover else kimi.KimiCodeObservationLost
    with pytest.raises(error) as caught:
        client._follow_prompt(session_id='kept-session', prompt_id='accepted', user_message_id='u',
            tools=[], before_usage={}, before_seq=0, started_at=0, emit=lambda _: None,
            live_stream=stream, detach_requested=lambda: handover and clock[0] >= 5)
    client._request.assert_not_called()
    return clock[0], caught.value, seen


def test_missing_server_stops_observation_without_resubmitting(monkeypatch, tmp_path):
    elapsed, error, _ = follow(monkeypatch, tmp_path)
    assert 10 <= elapsed < 16
    assert error.error_code == 'provider_observation_lost'
    # Even an enabled continuation policy must not turn uncertain effects
    # into another model request.
    with pytest.raises(kimi.KimiCodeObservationLost):
        provider_error_continuation(error, invocation=Mock(), context=Mock(),
                                    provider_id='kimi', turn_started=True)


def test_successful_observation_resets_the_outage_budget(monkeypatch, tmp_path):
    elapsed, _, seen = follow(monkeypatch, tmp_path, recover=True)
    assert any(6 <= t < 8 for t in seen)
    assert elapsed >= 17


def test_worker_handover_remains_prompt_preserving_during_outage(monkeypatch, tmp_path):
    elapsed, _, _ = follow(monkeypatch, tmp_path, handover=True)
    assert elapsed < 10

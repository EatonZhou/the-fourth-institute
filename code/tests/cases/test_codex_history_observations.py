"""Legacy transcript observations retain evidence without invented tool work."""
import copy
from unittest.mock import Mock

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.codex_history import response_items_from_segments
from management_portal.execution.codex_history_delivery import CodexHistoryDelivery
from management_portal.execution.session_history_import import validate_codex_preservation


def observation():
    return {
        'segment_type': 'tool', 'status': 'observed',
        'metadata': {'source': 'provider_transcript', 'kind': 'tool', 'tool_name': ''},
        'content': {'id': 'old-tool', 'kind': 'tool', 'name': 'exec', 'namespace': 'functions',
                    'call_id': 'old-call', 'turn_id': 'old-turn', 'event_line': 27,
                    'created_at': '2026-08-02T00:00:00Z', 'segment_ordinal': 9},
    }


def test_observation_replays_all_retained_fields_without_invented_call_or_outcome():
    original = observation()
    before = copy.deepcopy(original)
    with pytest.raises(ValueError, match='requires name and call_id'):
        validate_codex_preservation([original])
    assert original == before


@pytest.mark.parametrize('change', ['source', 'kind', 'status', 'content_kind', 'name', 'call_id', 'extra'])
def test_unknown_or_damaged_tool_records_still_fail_closed(change):
    segment = observation()
    if change in {'source', 'kind'}:
        segment['metadata'][change] = 'unproven'
    elif change == 'status':
        segment['status'] = 'completed'
    elif change == 'content_kind':
        segment['content']['kind'] = 'unknown'
    elif change == 'extra':
        segment['content']['result'] = 'must not disappear'
    else:
        segment['content'][change] = ''
    with pytest.raises(ValueError, match='requires name and call_id'):
        response_items_from_segments([segment])


def test_original_input_frozen_history_import_is_retryable_and_keeps_audit(isolated_portal_case):
    with isolated_portal_case():
        store = AgentDataStore()
        legacy = observation()
        sid = store.put_segment(**legacy, content_type='application/json')
        question = store.put_segment(segment_type='input', content='original historical input')
        refs = [{'segment_id': question}, {'segment_id': sid}]
        before = store.fork_context_segments(refs)
        sent = Mock()
        def delivery(execution):
            return CodexHistoryDelivery(
                references=refs, route_id='isolated', agent_id='resident-isolated',
                actor='test-owner', execution_id=execution, send=sent, store=store)
        for attempt in ('same-input-attempt-1', 'same-input-attempt-2'):
            with pytest.raises(ValueError, match='requires name and call_id'):
                delivery(attempt).initialize('same-thread')
        sent.assert_not_called()
        assert store.fork_context_segments(refs) == before


def test_malformed_tool_fails_before_import_intent_or_external_send(isolated_portal_case, monkeypatch):
    with isolated_portal_case():
        store = AgentDataStore()
        malformed = observation()
        malformed['content']['call_id'] = ''
        sid = store.put_segment(**malformed, content_type='application/json')
        sent = Mock()
        intent = Mock(wraps=store.put_operation_record)
        monkeypatch.setattr(store, 'put_operation_record', intent)
        with pytest.raises(ValueError, match='requires name and call_id'):
            CodexHistoryDelivery(
                references=[{'segment_id': sid}], route_id='isolated', agent_id='resident-isolated',
                actor='test-owner', execution_id='original-input', send=sent, store=store,
            ).initialize('same-thread')
        sent.assert_not_called()
        intent.assert_not_called()


@pytest.mark.parametrize('isolated', [False, True])
def test_same_input_preparation_keeps_identity_and_isolated_delivery_skips_history(monkeypatch, isolated):
    from management_portal.society_service import resident_execution_domain as domain
    record = {'resident_id': 'resident-isolated'}
    job = {'user_message_id': 'msg-original', 'lineage_id': 'lineage-test', 'message': 'current input'}
    monkeypatch.setattr(domain.resident_input_scope, 'get', lambda *args: {})
    monkeypatch.setattr(domain, '_lineage_delivery_profile', lambda _: {'isolated': True} if isolated else {})
    monkeypatch.setattr(domain, '_requires_initial_context_compaction', lambda _: False)
    monkeypatch.setattr(domain, '_initial_context_compaction_completed', lambda _: False)
    monkeypatch.setattr(domain, '_prepare_resident_prompt', lambda _, j: {**j, 'actual_sent_prompt': j['message']})
    monkeypatch.setattr(domain.resident_backend, 'execution_binding_id', lambda _: 'resident:resident-isolated')
    monkeypatch.setattr(domain.execution_port, 'sessions', lambda: Mock(has_context=Mock(return_value=False)))
    snapshot = Mock(return_value={'segments': [{'segment_id': 'retained-tool-observation'}]})
    monkeypatch.setattr(domain.execution_port, 'delivery_context_snapshot', snapshot)
    prepared = domain._portable_context_execution(record, job)
    assert prepared['user_message_id'] == job['user_message_id']
    assert ('fork_context_input' in prepared) is not isolated
    if isolated:
        snapshot.assert_not_called()
    else:
        snapshot.assert_called_once_with(record['resident_id'], message_id=job['user_message_id'], binding_id='resident:resident-isolated')
        assert prepared['fork_context_input']['segments'] == [
            {'segment_id': 'retained-tool-observation', 'provider_turn_id': ''}]

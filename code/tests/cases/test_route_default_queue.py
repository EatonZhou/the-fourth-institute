"""Route defaults retain busy deliveries without changing explicit policies.

:module: lineage.service
:design: decision_4adcc1ff2f2c
"""
import pytest

from management_portal.society_service import (
    lineage_event_claims, lineage_primitives, lineage_program,
    lineage_program_wire_queue as queues,
    lineage_domain, lineage_program_backlog,
)
from tests.cases.test_lineage_program_runtime_claims import lineage_id, _event
from tests.cases.test_lineage_program_runtime_integration import (
    runtime_store, ACTOR, WORK_A, _worker, _component, _wire,
)
from tests.cases import society_service_test_support as support


@pytest.mark.parametrize('policy', [None, 'drop', 'latest'])
def test_route_creation_defaults_to_queue_but_preserves_explicit_policy(policy):
    config = {} if policy is None else {'unavailable_policy': policy}
    result = lineage_program.normalize_program({
        'components': [_component('event_source_default', 'event_source', event_type='manual.triggered')],
        'wires': [_wire('wire_default', 'route', 'event_source_default', WORK_A, config=config)],
    }, lineage_nodes=[_worker(WORK_A)])['wires'][0]['config']
    assert result['unavailable_policy'] == (policy or 'queue')
    assert result['expires_after_seconds'] == 0
    assert result.get('queue_limit') == (10 if policy is None else None)
    assert config == ({} if policy is None else {'unavailable_policy': policy})


@pytest.mark.parametrize('event_type', ['manual.triggered', 'resident.work_finished'])
def test_unspecified_policy_retains_fifo_and_runtime_view(lineage_id, event_type):
    route = {'wire_id': 'wire_default', 'target_id': WORK_A, 'target_kind': 'resident', 'config': {}}
    for index in (1, 2):
        raw = dict(_event(index), type=event_type)
        event = lineage_primitives.normalize_event(raw)
        result = lineage_event_claims.defer_program_event_route(
            lineage_id, 'event_source_pending', route, event, f'cursor_{index}', reason='resident_running',
        )
        assert result['status'] == 'queued'
    view = queues.event_wire_runtime_view(lineage_id, {'wire_default': {}})['wire_default']
    assert (view['unavailable_policy'], view['queue_limit'], view['expires_after_seconds'], view['pending_count']) == ('queue', 10, 0, 2)
    for index in (1, 2):
        pending = queues.claim_pending_event(lineage_id, 'wire_default')
        assert pending['event']['id'] == f'event_{index}'
        assert queues.finish_pending_event(lineage_id, 'wire_default', pending['dispatch_token'], delivered=True)
    assert queues.claim_pending_event(lineage_id, 'wire_default') is None
    assert route['config'] == {}


def test_blank_queue_policy_uses_default(lineage_id):
    result = queues.defer_event_for_wire(
        lineage_id, 'event_source_pending', 'wire_blank', 'cursor_blank', _event(1), policy='',
    )
    assert result['status'] == 'queued'


def test_new_route_waits_for_resident_then_delivers_once(runtime_store, monkeypatch):
    busy = {'value': True}
    sent = []
    monkeypatch.setattr(support, 'NODE_ACTIVE_JOB_RESOLVER', lambda node, actor: busy['value'])
    monkeypatch.setattr(support, 'NODE_MESSAGE_SENDER', lambda node, prompt, actor: (
        sent.append(prompt) or {'message_id': f'job_default_{len(sent)}'}
    ))
    created = support.create_lineage_with_resident_members({
        'title': 'Default queue', 'nodes': [_worker(WORK_A)],
        'program': {
            'components': [_component('event_source_default', 'event_source', event_type='manual.triggered')],
            'wires': [_wire('wire_default', 'route', 'event_source_default', WORK_A)],
        },
    }, ACTOR)
    lid = created['lineage_id']
    for prompt in ('first default delivery', 'second default delivery'):
        lineage_domain.emit_program_manual_event(lid, {'event_source_id': 'event_source_default', 'prompt': prompt}, ACTOR)
    assert sent == []
    assert queues.event_wire_runtime_view(lid, {'wire_default': {}})['wire_default']['pending_count'] == 2
    busy['value'] = False
    authority = lineage_domain._program_control_authority()
    for _ in range(3):
        lineage_program_backlog.drain_event_backlog(lid, ACTOR, authority=authority)
    assert len(sent) == 2
    assert 'first default delivery' in sent[0]
    assert 'second default delivery' in sent[1]

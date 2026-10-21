"""Effect composition cannot discard invalid authored Message envelopes.

:module: lineage.hatg
:importance: P1
:aspects: functional, architecture-conformance
:design: decision_898b8911a2ff, decision_c524c775e0be
"""
import copy
import json

import pytest

from management_portal.society_service import lineage_primitives, lineage_program, resident_protocol
from management_portal.society_service.lineage_program_effect_rules import DEFAULT_CONTINUE_PROMPT, prepare_call_arguments
from tests.cases.test_effect_script import _lineage, _plan, _Host
from tests.cases.test_generic_effect import invocation, TARGET


INVALID = [
    {'text': 'lost fixed task'},
    {'text': 'lost', 'content_parts': [{'type': 'text', 'text': 'valid text'}]},
    {'content_parts': {'type': 'text', 'text': 'wrong container'}},
    {'content_parts': ['not a part object']},
    {'role': 'assistant', 'content_parts': []},
    ['not a Message'],
]


@pytest.mark.parametrize('message', INVALID)
def test_authored_message_constants_report_component_and_source_line(message):
    """Effect Message 校验与输入组合契约

    :design-point:
    """
    effect_id = 'effect_aaaaaaaaaaaa'
    script = '# saved invalid draft\nDeliverMessage resident_id="${endpoint.target}" message:=' + json.dumps(message)
    effect = _plan(script)['effects'][effect_id]
    diagnostics = lineage_program._effect_diagnostics(effect_id, effect)
    [error] = [item for item in diagnostics if item['code'] == 'effect.invalid_argument']
    assert error['item_id'] == effect_id and error['level'] == 'error'
    assert '第 2 行' in error['message']
    if isinstance(message, dict) and 'text' in message:
        assert 'unsupported fields: text' in error['message']


@pytest.mark.parametrize('message', INVALID)
def test_dynamic_invalid_message_fails_before_delivery_and_remains_failed_on_replay(monkeypatch, message):
    """Effect Message 校验与输入组合契约

    :design-point:
    """
    from management_common.principal import OperationIdentity
    from management_portal.portal_access_control import get_portal_access_store
    get_portal_access_store().upsert_user('CASE-A', actor='portal', status='active')
    from management_portal.hatg import routing
    from management_portal.society_service import lineage_program_effect_execution as execution, trigger_paths
    lineage = _lineage('lineage_aaaaaaaaaaaa')
    plan = _plan('DeliverMessage resident_id="${endpoint.target}" message="${arguments.message}"')
    assert not lineage_program.effect_script_analysis(plan['effects']['effect_aaaaaaaaaaaa']).diagnostics
    event = lineage_primitives.LineageEvent(id='event_message_validation', type='manual.triggered',
                                          payload={}, identity=OperationIdentity.for_actor('CASE-A').to_dict())
    activation = lineage_primitives.EffectInvocation(id='invocation_message_validation', activation=event,
                                                     arguments={'message': message, 'prompt': 'current input'})
    monkeypatch.setattr(routing, 'execute', lambda *a, **k: pytest.fail('invalid Message dispatched'))
    host = _Host()
    result = execution._execute_program_effect_component(lineage, plan, 'effect_aaaaaaaaaaaa', activation, 'CASE-A', host=host)
    assert result['status'] == 'failed' and result['typed_outputs']['retryable'] is False
    assert host.registered == []
    key = execution.effect_idempotency_key(lineage['lineage_id'], 'effect_aaaaaaaaaaaa', activation)
    finished = trigger_paths.effect_finished(lineage['lineage_id'], key)
    assert finished['status'] == 'failed'
    assert finished['calls']['c1']['status'] == 'failed'
    before = copy.deepcopy(finished)
    again = execution._execute_program_effect_component(lineage, plan, 'effect_aaaaaaaaaaaa', activation, 'CASE-A', host=host)
    assert again['replayed'] and again['status'] == 'failed'
    assert trigger_paths.effect_finished(lineage['lineage_id'], key) == before


@pytest.mark.parametrize('fixed', [None, '', {}, {'content_parts': []}, 'fixed task',
                                  {'content_parts': [{'type': 'text', 'text': 'fixed task'}]}])
@pytest.mark.parametrize('prompt', ['', 'current input'])
def test_valid_fixed_message_and_current_input_keep_composition(fixed, prompt):
    """Effect Message 校验与输入组合契约

    :design-point:
    """
    before = copy.deepcopy(fixed)
    prepared = prepare_call_arguments('DeliverMessage', {'resident_id': TARGET, 'message': fixed}, invocation(prompt=prompt))
    fixed_text = 'fixed task' if fixed == 'fixed task' or (isinstance(fixed, dict) and fixed.get('content_parts')) else ''
    expected = '\n\n'.join(part for part in (fixed_text, prompt) if part) or DEFAULT_CONTINUE_PROMPT
    assert prepared['message'] == {'role': 'user', 'content_parts': [{'type': 'text', 'text': expected}]}
    assert fixed == before


def test_valid_message_parts_and_metadata_survive_without_repeating_input():
    """Effect Message 校验与输入组合契约

    :design-point:
    """
    image = {'type': 'image', 'url': 'https://example.invalid/image.png'}
    reference = {'type': 'context_reference', 'resource_id': 'resident_bbbbbbbbbbbb'}
    fixed = {'message_id': 'msg_0123456789abcdef', 'quote': {'message_id': 'msg_fedcba9876543210'},
             'content_parts': [{'type': 'text', 'text': 'fixed task\n\ncurrent input'}, image]}
    prepared = prepare_call_arguments('DeliverMessage', {'resident_id': TARGET, 'message': fixed},
                                      invocation(prompt='current input', content_parts=[reference]))
    assert prepared['message']['message_id'] == fixed['message_id']
    assert prepared['message']['quote'] == fixed['quote']
    assert prepared['message']['content_parts'] == [fixed['content_parts'][0], image, reference]
    parsed = resident_protocol._input_parts(prepared['message'])
    assert parsed[0] == 'fixed task\n\ncurrent input' and parsed[1] == [image]


def test_formal_delivery_and_effect_use_same_part_container_rejection():
    """Effect Message 校验与输入组合契约

    :design-point:
    """
    bad = {'content_parts': [{'type': 'text', 'text': 'task'}, 42]}
    with pytest.raises(ValueError, match='items must be objects'):
        resident_protocol._input_parts(bad)
    with pytest.raises(ValueError, match='items must be objects'):
        prepare_call_arguments('DeliverMessage', {'resident_id': TARGET, 'message': bad}, invocation())

"""Observe a terminal workflow delivery rejection using the original ISA receipts."""

import copy

import pytest

from management_portal import lineage_workflows


@pytest.fixture
def original_workflow(monkeypatch):
    handle = {
        'resident_id': 'resident_000000000001', 'lineage_id': 'lineage_000000000001',
        'event_source_id': 'event_source_original', 'effect_id': 'effect_original',
        'event_id': 'event_original', 'trigger_path_id': 'trigger_path_original',
        'instruction_id': 'inst_lin_trigger_original', 'request_id': 'msg_original',
        'instruction': 'TriggerEvent',
    }
    call = {
        'instruction': 'DeliverMessage', 'instruction_id': 'inst_lin_deliver_original', 'status': 'failed',
        'business_ids': {}, 'error': {
            'code': 'instruction.forbidden', 'commit_state': 'not_committed', 'retryable': False,
            'message': 'Resident 工作目录必须是已绑定的 Workspace',
        },
    }
    path = {
        'lineage_id': handle['lineage_id'], 'trigger_path_id': handle['trigger_path_id'],
        'root_kind': 'event', 'root_node_id': handle['event_source_id'],
        'cause_kind': 'instruction', 'cause_id': handle['instruction_id'], 'occurrence_id': handle['event_id'],
        'status': 'failed', 'finished_at': '2026-11-19T08:52:54Z', 'revision': 14,
    }
    event = {'step_id': 'event_step', 'kind': 'event', 'node_id': handle['event_source_id'],
             'activation_key': handle['event_id'], 'status': 'failed', 'finished_at': path['finished_at']}
    effect = {'step_id': 'effect_step', 'kind': 'effect', 'node_id': handle['effect_id'],
              'activation_key': 'effect:original', 'status': 'failed', 'finished_at': path['finished_at'],
              'result': {'calls': {'c1': call}, 'failure': {'completed_calls': 0, 'retryable': False}}}
    page = {'path': path, 'steps': {'items': [event, effect], 'next_offset': None},
            'edges': {'items': [{'source_step_id': 'event_step', 'target_step_id': 'effect_step'}], 'next_offset': None},
            'execution_links': {'items': [], 'next_offset': None}, 'executions': {'items': [], 'next_offset': None}}
    lookups = []

    def read(actor, instruction, arguments, *, entry):
        assert actor == 'original-actor' and entry == 'web'
        lookups.append((instruction, arguments))
        if instruction == 'GetTriggerPath':
            assert arguments['trigger_path_id'] == 'trigger_path_original'
            return page
        assert instruction == 'GetInputState'
        return {'input_state': {'message_id': arguments['message_id'], 'state': 'queued'}}

    monkeypatch.setattr(lineage_workflows.isa_client, 'call', read)
    return handle, page, call, lookups


def test_terminal_delivery_rejection_preserves_both_receipts(original_workflow):
    handle, _, call, lookups = original_workflow
    result = lineage_workflows.WorkflowBindings()._read_submission_path(handle, actor='original-actor', entry='web')

    assert result['submission_state'] == 'not_submitted'
    assert result['instruction_id'] == handle['instruction_id']
    assert result['submission_failure_kind'] == 'delivery_rejected'
    assert result['submission_failure_instruction_id'] == call['instruction_id']
    assert result['submission_failure_code'] == call['error']['code']
    assert result['submission_failure_commit_state'] == 'not_committed'
    assert result['submission_reason'] == call['error']['message']
    assert 'message_id' not in result
    assert [name for name, _ in lookups] == ['GetTriggerPath']


@pytest.mark.parametrize('change', [
    'wrong_event', 'no_original_anchor', 'incomplete_page', 'unknown_commit', 'interrupted', 'in_flight_call',
    'retryable', 'another_call', 'another_effect', 'still_running', 'business_reference', 'execution_link',
    'other_resident_input', 'missing_journal', 'manual_disposition',
])
def test_ambiguous_workflow_remains_pending(original_workflow, change):
    handle, page, call, _ = original_workflow
    effect = page['steps']['items'][1]
    if change == 'wrong_event':
        page['path']['occurrence_id'] = 'different_event'
    elif change == 'no_original_anchor':
        handle.pop('instruction_id')
        handle.pop('event_id')
    elif change == 'incomplete_page':
        page['edges']['next_offset'] = 1
    elif change == 'unknown_commit':
        call['error']['commit_state'] = 'unknown'
    elif change == 'interrupted':
        call['error']['code'] = 'instruction.interrupted'
    elif change == 'in_flight_call':
        call['status'] = 'unresolved'
    elif change == 'retryable':
        call['error']['retryable'] = True
    elif change == 'another_call':
        effect['result']['calls']['c2'] = {**call, 'instruction_id': 'inst_lin_other_call'}
    elif change == 'another_effect':
        page['steps']['items'].append({**effect, 'step_id': 'other_effect_step', 'activation_key': 'effect:another'})
    elif change == 'still_running':
        effect['status'] = 'running'
    elif change == 'business_reference':
        call['business_ids']['message_id'] = 'msg_other'
    elif change == 'execution_link':
        page['execution_links']['items'].append({'step_id': 'effect_step', 'execution_id': 'execution-other'})
    elif change == 'other_resident_input':
        page['steps']['items'].append({'step_id': 'other_resident_step', 'kind': 'resident',
                                      'node_id': 'resident_other', 'input_message_id': 'msg_other'})
    elif change == 'missing_journal':
        effect['result']['calls'] = {}
    elif change == 'manual_disposition':
        effect['result']['disposition'] = {'status': 'failed'}

    result = lineage_workflows.WorkflowBindings()._read_submission_path(handle, actor='original-actor', entry='web')

    assert result['submission_state'] == 'pending'
    assert 'submission_failure_kind' not in result


def test_confirmed_input_takes_precedence_over_rejection(original_workflow):
    handle, page, _, lookups = original_workflow
    page['steps']['items'].append({'step_id': 'resident_step', 'kind': 'resident', 'node_id': handle['resident_id'],
                                  'input_message_id': 'msg_accepted', 'status': 'queued'})
    page['edges']['items'].append({'source_step_id': 'effect_step', 'target_step_id': 'resident_step'})

    result = lineage_workflows.WorkflowBindings()._read_submission_path(handle, actor='original-actor', entry='web')

    assert result['submission_state'] == 'submitted'
    assert result['message_id'] == 'msg_accepted'
    assert [name for name, _ in lookups] == ['GetTriggerPath', 'GetInputState']


@pytest.mark.parametrize('change_revision', [False, True])
def test_terminal_rejection_requires_stable_complete_pagination(original_workflow, monkeypatch, change_revision):
    handle, first, _, _ = original_workflow
    # Normal fresh trigger handles retain the event and path even before the
    # original instruction ID has been recovered.
    parent_instruction_id = handle.pop('instruction_id')
    second = copy.deepcopy(first)
    first['steps']['items'] = first['steps']['items'][:1]
    first['steps']['next_offset'] = 1
    second['steps']['items'] = second['steps']['items'][1:]
    second['edges']['items'] = []
    if change_revision:
        second['path']['revision'] += 1

    def read(actor, instruction, arguments, *, entry):
        assert instruction == 'GetTriggerPath'
        return {0: first, 1: second}[arguments['offset']]

    monkeypatch.setattr(lineage_workflows.isa_client, 'call', read)

    result = lineage_workflows.WorkflowBindings()._read_submission_path(handle, actor='original-actor', entry='web')

    if change_revision:
        assert result['submission_state'] == 'pending'
    else:
        assert result['submission_state'] == 'not_submitted'
        assert result['instruction_id'] == parent_instruction_id

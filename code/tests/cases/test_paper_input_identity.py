from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from management_portal import paper_backend, paper_projection, resident_handoff
from tests.cases.test_paper_figure_transactions import figure_store
from tests.cases.test_paper_deep_read_execution_boundary import _install_job


HANDLE = {'resident_id': 'resident_fixture', 'lineage_id': 'lineage_fixture', 'message_id': 'msg_actual_input',
          'event_id': 'event_fixture', 'effect_id': 'effect_fixture', 'trigger_path_id': 'trigger_path_123456789abc'}


def result():
    return {**HANDLE, 'message': 'Verified generated content', 'usage': {'input_tokens': 12, 'message_id': 'provider_usage_id'},
            'messages': [
                {'message_id': 'msg_reply_one', 'role': 'agent', 'reply_to_message_id': HANDLE['message_id']},
                {'message_id': 'msg_reply_final', 'role': 'agent', 'reply_to_message_id': HANDLE['message_id'], 'usage': {'input_tokens': 12}},
            ]}


@pytest.mark.parametrize('requested', ['msg_request_only', ''])
@pytest.mark.parametrize('role', list(paper_projection.ROLES))
def test_all_paper_roles_share_input_reply_and_usage_identity(monkeypatch, requested, role):
    """论文各工作角色统一保存输入回复与用量身份"""
    records = []
    raw = result()
    original = deepcopy(raw)

    def native(name, **options):
        assert name == 'paper' and options['workflow']['name'] == role
        options['on_assigned']({'resident_id': HANDLE['resident_id'], 'lineage_id': HANDLE['lineage_id']})
        assert records[-1]['input_message_id'] == ''
        assert records[-1]['requested_message_id'] == requested
        options['on_submitted'](HANDLE)
        assert records[-1]['input_message_id'] == HANDLE['message_id']
        return raw

    monkeypatch.setattr(paper_projection.native, 'turn', native)
    answer = paper_projection.turn(
        actor='alice', resident_scope=role, **({'message_id': requested} if requested else {}),
        on_assigned=records.append, on_submitted=records.append,
    )
    receipt = answer['invocation']
    assert receipt['input_message_id'] == HANDLE['message_id']
    assert receipt['schema'] == paper_projection.PAPER_INPUT_RECEIPT_SCHEMA
    assert receipt['trigger_path_id'] == HANDLE['trigger_path_id']
    assert 'invocation_id' not in receipt
    assert 'message_id' not in receipt
    assert receipt['requested_message_id'] == requested
    assert receipt['reply_message_ids'] == ['msg_reply_one', 'msg_reply_final']
    assert receipt['usage_message_id'] == 'msg_reply_final'
    assert answer['usage']['input_message_id'] == HANDLE['message_id']
    assert 'message_id' not in answer['usage']
    assert answer['usage']['usage_reported_message_id'] == 'provider_usage_id'
    assert answer['usage']['input_tokens'] == 12
    assert records[-1] == receipt
    assert raw == original


def test_queued_effect_id_is_not_an_input_until_actual_delivery(monkeypatch):
    """排队中的 Effect 计划消息在实际投递前不视为输入"""
    records = []

    def native(_name, **options):
        options['on_submitted']({**HANDLE, 'message_id': 'msg_planned_only', 'status': 'queued'})
        assert records[-1]['input_identity_status'] == 'unconfirmed'
        assert records[-1]['input_message_id'] == ''
        assert records[-1]['planned_message_id'] == 'msg_planned_only'
        return result()

    monkeypatch.setattr(paper_projection.native, 'turn', native)
    answer = paper_projection.turn(actor='alice', resident_scope='paper:figure', on_submitted=records.append)
    assert records[-1]['input_message_id'] == HANDLE['message_id']
    assert records[-1]['planned_message_id'] == 'msg_planned_only'
    assert answer['usage']['input_message_id'] == HANDLE['message_id']


@pytest.mark.parametrize('field', ['resident_id', 'lineage_id', 'message_id'])
def test_confirmed_associations_cannot_change_in_completion(monkeypatch, field):
    """论文调用完成结果不得改变已确认身份绑定"""
    records = []

    def native(_name, **options):
        options['on_submitted'](HANDLE)
        return {**result(), field: 'different_identity'}

    monkeypatch.setattr(paper_projection.native, 'turn', native)
    with pytest.raises(ValueError, match='confirmed'):
        paper_projection.turn(actor='alice', resident_scope='paper:figure', on_submitted=records.append)
    assert len(records) == 1 and records[0]['input_message_id'] == HANDLE['message_id']


def test_missing_input_id_cannot_be_replaced_by_a_reply_or_usage_id(monkeypatch):
    """论文输入标识缺失时不得用回复或用量标识替代"""
    raw = result()
    raw.pop('message_id')
    monkeypatch.setattr(paper_projection.native, 'turn', Mock(return_value=raw))
    with pytest.raises(ValueError, match='canonical Resident input identity'):
        paper_projection.turn(actor='alice', resident_scope='paper:figure')


def test_usage_id_does_not_prove_a_reply_when_reply_projection_is_absent(monkeypatch):
    """缺少回复投影时用量标识不能证明回复身份"""
    raw = result()
    raw.pop('messages')
    monkeypatch.setattr(paper_projection.native, 'turn', Mock(return_value=raw))
    answer = paper_projection.turn(actor='alice', resident_scope='paper:summary')
    assert answer['reply_message_ids'] == [] and answer['usage_message_id'] == ''
    assert answer['usage']['usage_reported_message_id'] == 'provider_usage_id'


@pytest.mark.parametrize('field,value', [
    ('role', 'assistant'), ('role', 'user'), ('role', 'system'),
    ('reply_to_message_id', None), ('reply_to_message_id', 'msg_other_input'),
    ('message_id', ''),
])
def test_reply_metadata_rejects_noncanonical_or_uncorrelated_messages(monkeypatch, field, value):
    """论文回复元数据拒绝非标准角色与未关联输入的消息"""
    raw = result()
    raw['messages'][0][field] = value
    monkeypatch.setattr(paper_projection.native, 'turn', Mock(return_value=raw))
    with pytest.raises(paper_projection.PaperInputIdentityError, match='Resident reply'):
        paper_projection.turn(actor='alice', resident_scope='paper:summary')


def test_input_cannot_be_relabelled_as_a_resident_reply(monkeypatch):
    """论文输入消息不得同时作为 Resident 回复"""
    raw = result()
    raw['messages'] = [{'role': 'agent', 'message_id': HANDLE['message_id'], 'reply_to_message_id': HANDLE['message_id']}]
    monkeypatch.setattr(paper_projection.native, 'turn', Mock(return_value=raw))
    with pytest.raises(ValueError, match='also identify a Resident reply'):
        paper_projection.turn(actor='alice', resident_scope='paper:figure')


@pytest.mark.parametrize('generated_id', ['', 'gen-one'])
def test_figure_persists_delivered_id_before_wait_and_reconciles_on_resume(figure_store, monkeypatch, generated_id):
    """插图在等待前保存实际输入身份且恢复时补齐回复关联"""
    paper, path = figure_store
    figure = json.loads(path.read_text())[0]
    key = 'invocation' if generated_id else 'explanation_invocation'

    def saved():
        current = json.loads(path.read_text())[0]
        return (current['generated'][0] if generated_id else current)[key]

    def native(_name, **options):
        options['on_assigned']({'resident_id': HANDLE['resident_id'], 'lineage_id': HANDLE['lineage_id']})
        assert saved()['input_message_id'] == ''
        assert saved()['requested_message_id'] == options['message_id']
        options['on_submitted'](HANDLE)
        assert saved()['input_message_id'] == HANDLE['message_id']
        assert saved()['requested_message_id'] != HANDLE['message_id']
        raise SystemExit('worker lost after delivery')

    submit = Mock(side_effect=native)
    monkeypatch.setattr(paper_projection.native, 'turn', submit)
    with pytest.raises(SystemExit):
        paper_backend._run_figure_resident('Explain', paper, figure, 'explain', generated_id=generated_id)
    figure = json.loads(path.read_text())[0]
    observed = Mock(return_value=result())
    monkeypatch.setattr(resident_handoff, 'resume_and_wait', observed)
    content, usage = paper_backend._run_figure_resident('Unused', paper, figure, 'explain', generated_id=generated_id)
    assert content == 'Verified generated content'
    assert observed.call_args.kwargs['message_id'] == HANDLE['message_id']
    submit.assert_called_once()
    receipt = saved()
    assert receipt['reply_message_ids'] == usage['reply_message_ids'] == ['msg_reply_one', 'msg_reply_final']
    assert receipt['usage_message_id'] == usage['usage_message_id'] == 'msg_reply_final'
    assert receipt['input_message_id'] == usage['input_message_id'] == HANDLE['message_id']
    for key in ('event_id', 'effect_id', 'trigger_path_id'):
        assert receipt[key] == HANDLE[key]
    assert json.loads(Path(usage['events_path']).read_text())['input_message_id'] == HANDLE['message_id']


def test_resume_rejects_a_different_binding_before_any_input_lookup(monkeypatch):
    """论文恢复在查询输入前拒绝不同绑定"""
    observed = Mock()
    monkeypatch.setattr(resident_handoff, 'resume_and_wait', observed)
    with pytest.raises(paper_projection.PaperInputIdentityError):
        paper_projection.resume(
            actor='alice',
            resident_id=HANDLE['resident_id'],
            message_id='msg_different',
            receipt=paper_projection.receipt_from_resident_handle(HANDLE),
        )
    observed.assert_not_called()


def test_unconfirmed_figure_never_looks_up_planned_id_or_resubmits(figure_store, monkeypatch):
    """未确认插图输入不查询计划标识且不重新提交"""
    paper, path = figure_store
    figure = json.loads(path.read_text())[0]
    figure['explanation_invocation'] = paper_projection.receipt_from_resident_handle({**HANDLE, 'message_id': 'msg_planned', 'status': 'queued'})
    submit, observe = Mock(), Mock()
    monkeypatch.setattr(paper_projection.native, 'turn', submit)
    monkeypatch.setattr(resident_handoff, 'resume_and_wait', observe)
    with pytest.raises(RuntimeError, match='unconfirmed'):
        paper_backend._run_figure_resident('Unused', paper, figure, 'explain')
    submit.assert_not_called()
    observe.assert_not_called()


def test_missing_bound_input_remains_an_input_error_not_permission_or_resubmission(figure_store, monkeypatch):
    """已绑定论文输入缺失时保留输入错误且不重提交"""
    paper, path = figure_store
    figure = json.loads(path.read_text())[0]
    figure['explanation_invocation'] = {
        **paper_projection.receipt_from_resident_handle(HANDLE),
        'actor': 'alice',
    }
    submit = Mock()
    monkeypatch.setattr(paper_projection.native, 'turn', submit)
    monkeypatch.setattr(resident_handoff, 'resume_and_wait', Mock(side_effect=KeyError(HANDLE['message_id'])))
    with pytest.raises(KeyError):
        paper_backend._run_figure_resident('Unused', paper, figure, 'explain')
    submit.assert_not_called()


def test_paper_input_receipt_requires_the_exact_current_schema():
    """论文输入回执必须采用当前精确 schema"""
    with pytest.raises(paper_projection.PaperInputIdentityError, match='requires schema'):
        paper_projection.input_receipt({**HANDLE, 'input_message_id': HANDLE['message_id']})
    receipt = paper_projection.receipt_from_resident_handle(HANDLE)
    assert paper_projection.input_receipt(receipt) == receipt
    assert 'message_id' not in receipt


@pytest.mark.parametrize('outcome', ['completed', 'queued', 'identity_conflict'])
def test_deep_read_uses_same_canonical_receipt_and_never_retries_unconfirmed_delivery(monkeypatch, tmp_path, outcome):
    """深读使用统一输入回执且未确认投递失败后不重试"""
    state = {'job_id': 'deep-read-fixture', 'paper_id': 'paper_0123456789ab', 'status': 'queued', 'created_by': 'alice'}
    _install_job(monkeypatch, tmp_path, state)

    def native(_name, **options):
        options['on_assigned']({'resident_id': HANDLE['resident_id'], 'lineage_id': HANDLE['lineage_id']})
        assert state.get('resident_input_message_id', '') == ''
        if outcome == 'queued':
            options['on_submitted']({**HANDLE, 'status': 'queued', 'message_id': 'msg_planned'})
            raise RuntimeError('workflow accepted but not delivered')
        options['on_submitted'](HANDLE)
        assert state['resident_input_message_id'] == HANDLE['message_id']
        assert 'resident_message_id' not in state
        if outcome == 'identity_conflict':
            return {**result(), 'message_id': 'msg_wrong_input'}
        return {**result(), 'message': '# Paper\n' + paper_backend.DEEP_READ_HEADINGS}

    submit = Mock(side_effect=native)
    monkeypatch.setattr(paper_projection.native, 'turn', submit)
    paper_backend._run_deep_read_job(state['job_id'])
    submit.assert_called_once()
    if outcome == 'queued':
        assert state['status'] == 'error'
        assert state.get('resident_input_message_id', '') == ''
        assert 'resident_message_id' not in state
        assert state['resident_handoff']['planned_message_id'] == 'msg_planned'
    elif outcome == 'identity_conflict':
        assert state['status'] == 'error'
        assert state['resident_input_message_id'] == HANDLE['message_id']
    else:
        assert state['status'] == 'done'
        assert state['resident_reply_message_ids'] == ['msg_reply_one', 'msg_reply_final']
        assert state['resident_usage_message_id'] == 'msg_reply_final'

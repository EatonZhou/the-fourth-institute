from dataclasses import replace
import pytest
from management_portal.agent_prompt_context import system_parts_revision
from management_portal.execution.contracts import InjectionPart, SystemInstructions
from management_portal.society_service import resident_execution_domain as domain
from management_portal.society_service import resident_execution_engine_domain as engine
from management_portal.society_service import resident_execution_work as work
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401

CONSTRAINT = {'version': 1, 'text_only': True, 'context': 'fresh'}


@pytest.mark.parametrize('change', [{'version': 2}, {'extra': True}, {'context': 'session'}, {'text_only': False}])
def test_formal_constraints_reject_unknown_contract(resident_record, change):
    from tests.cases.isa_test_support import run
    response = run('DeliverMessage', {'resident_id': resident_record['resident_id'],
        'message': {'content_parts': [{'type': 'text', 'text': 'only text'}]},
        'context_policy': 'fresh', 'execution_constraints': {**CONSTRAINT, **change}}, actor='CASE-A')
    assert response.status == 'failed'


def test_actual_parts_revision_covers_content_order_and_lifecycle():
    a = InjectionPart(name='rules', content='中文', timing='every_turn', placement='system')
    b = InjectionPart(name='identity', content='who', timing='session_start', placement='prompt_prefix')
    original = SystemInstructions(parts=(a, b))
    digest = system_parts_revision(original)
    assert digest == system_parts_revision(SystemInstructions(parts=(a, b)))
    for parts in ((b, a), (replace(a, content='new'), b), (replace(a, timing='session_start'), b),
                  (replace(a, placement='prompt_suffix'), b), (replace(a, name='other'), b)):
        assert digest != system_parts_revision(SystemInstructions(parts=parts))


@pytest.mark.parametrize('field', ['input_media', 'context_refs', 'additional_skills', 'runtime_context', 'fork_context_input'])
def test_nontext_rejected_before_request_factory(field, monkeypatch):
    monkeypatch.setattr(domain, '_build_resident_execution_request_unrestricted', lambda *a: pytest.fail('must reject first'))
    with pytest.raises(ValueError, match=field):
        domain._build_resident_execution_request({}, {'execution_constraints': CONSTRAINT,
            'raw_message': 'text', field: ['external']})


def test_fresh_and_ordinary_compatibility_and_durable_recovery(resident_record, monkeypatch):
    record = resident_record
    job = {'execution_id': 'execution-' + '6' * 24, 'actor': 'CASE-A',
           'user_message_id': 'msg_text_constraint', 'raw_message': 'only explicit text',
           'actual_sent_prompt': 'only explicit text', 'queue': []}
    monkeypatch.setattr(domain, '_lineage_delivery_profile', lambda j: {})
    ordinary = domain._build_resident_execution_request(record, job)
    assert 'execution_constraints' not in ordinary.runtime_options
    restricted = {**{k: v for k, v in job.items() if not k.startswith('_')}, 'execution_constraints': dict(CONSTRAINT)}
    with pytest.raises(ValueError, match='fresh'):
        domain._build_resident_execution_request(record, restricted)
    monkeypatch.setattr(domain, '_lineage_delivery_profile', lambda j: {'isolated': True})
    work.write(record, restricted)
    assert work.read(record)['execution_constraints'] == CONSTRAINT
    request = engine.durable_resident_execution_request(record, restricted,
        lambda: domain._build_resident_execution_request(record, restricted))
    assert request.session.mode == 'stateless' and not request.session.binding_id
    assert request.runtime_options['execution_constraints'] == CONSTRAINT
    assert request.runtime_options['resident_assembly']['system_parts_revision'] == system_parts_revision(request.system_instructions)
    assert 'effective_tools' not in request.runtime_options  # no enforcement claim
    recovered = engine.durable_resident_execution_request(record, work.read(record),
        lambda: pytest.fail('must recover immutable request'))
    assert recovered.to_payload() == request.to_payload()


def test_current_text_snapshot_uses_actual_assembly_and_invalidates(resident_record, monkeypatch):
    from management_portal.society_service import resident_runtime_domain
    record = resident_record
    rid = record['resident_id']
    monkeypatch.setattr(domain, '_lineage_delivery_profile', lambda j: {'isolated': True})
    first = domain.text_only_input_snapshot(rid, 'CASE-A', text='explicit text')
    second = domain.text_only_input_snapshot(rid, 'CASE-A', text='explicit text')
    assert first == second
    job = {'actor': 'CASE-A', 'user_message_id': 'msg_real_snapshot', 'raw_message': 'explicit text',
           'execution_constraints': dict(CONSTRAINT)}
    request = domain._build_resident_execution_request(record, job)
    assert system_parts_revision(request.system_instructions) == first['system_parts_revision']
    settings = resident_runtime_domain.read_resident_runtime_settings(record)
    resident_runtime_domain.write_resident_runtime_settings(record, {**settings,
        'prompt_context': {'system_prompt': 'new fixed translation policy'}})
    changed = domain.text_only_input_snapshot(rid, 'CASE-A', text='explicit text')
    assert changed['settings_revision'] != first['settings_revision']
    assert changed['system_parts_revision'] != first['system_parts_revision']


def test_formal_delivery_constraints_enter_actual_job(resident_record, monkeypatch):
    from tests.cases.isa_test_support import run
    from management_portal.society_service import resident_intake_domain, resident_protocol
    from management_portal.society_service import lineage_program_resident_inputs
    record = resident_record
    monkeypatch.setattr(work, 'start', lambda *a, **kw: None)
    result = run('DeliverMessage', {'resident_id': record['resident_id'],
        'message': {'content_parts': [{'type': 'text', 'text': 'short synthetic text'}]},
        'context_policy': 'fresh', 'execution_constraints': CONSTRAINT}, actor='CASE-A', key='constrained-input').raise_for_error()
    job = work.read(record)
    profile = lineage_program_resident_inputs.delivery_profile(record['lineage_id'], result['message_id'])
    assert profile['execution_constraints'] == CONSTRAINT
    request = domain._build_resident_execution_request(record, job)
    assert request.runtime_options['execution_constraints'] == CONSTRAINT
    assert request.session.mode == 'stateless'


def test_snapshot_formal_rpc_is_readonly_and_uses_caller(resident_record):
    from tests.cases.isa_test_support import run
    args = {'resident_id': resident_record['resident_id'], 'text': 'synthetic only'}
    first = run('GetResidentTextInputSnapshot', args, actor='CASE-A').raise_for_error()
    second = run('GetResidentTextInputSnapshot', args, actor='CASE-A').raise_for_error()
    assert first == second and first['system_parts_revision']
    assert not work.read(resident_record).get('execution_id')
    assert run('GetResidentTextInputSnapshot', {**args, 'text': ''}, actor='CASE-A').status == 'failed'
    assert run('GetResidentTextInputSnapshot', {**args, 'actor': 'CASE-A'}, actor='CASE-B').status == 'failed'

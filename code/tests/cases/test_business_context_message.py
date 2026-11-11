"""Real disposable message owners; no execution/model or production mutation."""
import pytest
from tests.cases.test_paper_research_deleted import owners, ACTOR, prepare
from management_portal.business_context import message_metadata
from management_portal import paper_chat_research as research


def test_metadata_in_formal_message_json(owners, monkeypatch):
    from management_portal import paper_chat_research as research
    from management_portal.society_service import resident_conversation_stream
    _, create = prepare(owners, monkeypatch)
    slot = create('Metadata storage fixture')
    metadata = {'business_context': {'application':'paper.deep-read', 'subject_ref':'paper_0123456789ab',
                'group_ref':'group-example', 'attempt_ref':'attempt-example'}}
    message = {'message_id':'msg_12121212121212121212121212121212', 'role':'user',
               'content_parts':[{'type':'text','text':'Disposable input'}], 'metadata':metadata}
    result = research.isa_client.call(ACTOR,'DeliverMessage',{'resident_id':slot['resident_id'],
        'message':message,'activation_policy':'attach_to_next'},key='business-metadata-test')
    stored = resident_conversation_stream.get_repository().messages.stored_message(slot['resident_id'],result['message_id'])
    assert stored['metadata'] == metadata
    assert stored['content_parts'] == message['content_parts']
    repository = resident_conversation_stream.get_repository()
    repository.messages.commit_user_message(slot['resident_id'], stored)
    assert repository.messages.stored_message(slot['resident_id'], result['message_id'])['metadata'] == metadata
    from copy import deepcopy
    conflict = deepcopy(stored)
    conflict['metadata']['business_context']['group_ref'] = 'another-group'
    from management_portal.society_service.resident_message_contract import MessageIdentityConflict
    with pytest.raises(MessageIdentityConflict):
        repository.messages.validate_message_identity(slot['resident_id'], conflict)


def test_workflow_effect_keeps_same_business_context():
    from types import SimpleNamespace
    from management_portal.society_service.lineage_program_effect_rules import _delivery_message
    metadata = {'business_context': {'application': 'paper.review', 'subject_ref': 'paper_0123456789ab',
                'group_ref': 'review-group', 'attempt_ref': 'review-job'}}
    message = _delivery_message({'role': 'user', 'content_parts': [{'type': 'text', 'text': 'Role'}]},
        SimpleNamespace(arguments={'prompt': 'Task', 'message_metadata': metadata}))
    assert message['metadata'] == metadata
    assert message['content_parts'] == [{'type': 'text', 'text': 'Role\n\nTask'}]


def test_real_workflow_trigger_retains_metadata_and_replay(owners, monkeypatch):
    from management_portal.lineage_workflows import WorkflowBindings
    from management_portal.society_service import resident_conversation_stream
    _, create = prepare(owners, monkeypatch)
    slot = create('Workflow history fixture')
    workflows = WorkflowBindings()
    binding = workflows.install(actor=ACTOR, resident_id=slot['resident_id'], lineage_id=slot['lineage_id'],
        name='history-fixture', title='Independent fixture', prompt='Short role prompt', context_policy='fresh')
    metadata = {'business_context': {'application': 'paper.review', 'subject_ref': 'request:' + ACTOR + ':fixture',
        'group_ref': 'same-group', 'attempt_ref': 'workflow-attempt'}}
    result = workflows.trigger(binding, actor=ACTOR, prompt='Only this task', request_id='history-workflow-once',
        message_metadata=metadata)
    again = workflows.trigger(binding, actor=ACTOR, prompt='Only this task', request_id='history-workflow-once',
        message_metadata=metadata)
    assert result['message_id'] == again['message_id']
    from copy import deepcopy
    second_metadata = deepcopy(metadata)
    second_metadata['business_context']['attempt_ref'] = 'second-workflow-attempt'
    second = workflows.trigger(binding, actor=ACTOR, prompt='Second independent task', request_id='history-workflow-second',
        message_metadata=second_metadata)
    assert second['resident_id'] == result['resident_id'] == slot['resident_id']
    assert second['message_id'] != result['message_id']
    repository = resident_conversation_stream.get_repository()
    stored = repository.messages.stored_message(slot['resident_id'], result['message_id'])
    assert stored['metadata'] == metadata
    from management_portal.society_service.lineage_program_resident_inputs import delivery_profile
    assert delivery_profile(slot['lineage_id'], result['message_id'])['isolated'] is True
    assert delivery_profile(slot['lineage_id'], second['message_id'])['isolated'] is True
    assert repository.messages.stored_message(slot['resident_id'], second['message_id'])['metadata'] == second_metadata
    from management_portal.society_service.resident_business_history import default_view
    from management_portal.society_service import resident_protocol
    assert default_view(resident_protocol._target(slot['resident_id'], ACTOR), repository) == 'attempts'


def test_attempt_list_subject_acl_and_detail_reauthorize(owners, monkeypatch):
    from management_portal.society_service import resident_conversation_stream
    from management_portal.society_service import resident_business_history as history
    from management_portal import paper_business_history
    _, create = prepare(owners, monkeypatch)
    slot = create('Attempt history fixture')
    repository = resident_conversation_stream.get_repository()
    contexts = []
    for index, who in enumerate([ACTOR, ACTOR, 'other-caller']):
        context = {'application': 'paper.ccf', 'subject_ref': 'request:' + who + ':catalogue',
                   'group_ref': 'ccf-group-' + who, 'attempt_ref': 'ccf-attempt-' + str(index)}
        contexts.append(context)
        message = {'message_id': 'msg_' + str(index + 3) * 32, 'role': 'user', 'author_id': ACTOR,
            'content_parts': [{'type': 'text', 'text': 'Private catalogue input'}],
            'metadata': {'business_context': context}}
        repository.messages.commit_user_message(slot['resident_id'], message)
        repository.set_input_state(slot['resident_id'], message['message_id'], 'queued')
    call = research.isa_client.call
    resolved = []
    def owner(actor, instruction, arguments, **kwargs):
        if instruction == 'ResolveMessageContextReferences':
            resolved.append(actor)
            return {'snapshot': paper_business_history.resolve(actor=actor,
                target_resident_id=arguments['target_resident_id'], reference=arguments['reference'])}
        return call(actor, instruction, arguments, **kwargs)
    monkeypatch.setattr(research.isa_client, 'call', owner)
    result = owner(ACTOR, 'ListMessages', {'resident_id': slot['resident_id'], 'view': 'attempts', 'cursor': ''})
    assert [row['business_context'] for row in result['attempts']] == [contexts[1], contexts[0]]
    assert resolved == [ACTOR, ACTOR]
    first = history.page(slot['resident_id'], ACTOR, limit=1)
    second = history.page(slot['resident_id'], ACTOR, limit=1, cursor=first['next_page_token'])
    assert [first['attempts'][0]['input_message_id'], second['attempts'][0]['input_message_id']] == ['msg_'+'4'*32, 'msg_'+'3'*32]
    assert not second['next_page_token']
    assert 'total' not in first
    detail = owner(ACTOR, 'ListMessages', {'resident_id': slot['resident_id'], 'view': 'attempts',
        'cursor': '', 'input_message_id': 'msg_' + '3' * 32})
    assert len(detail['conversation']['messages']) == 1
    assert 'queue' not in detail['conversation']
    def revoked(*args, **kwargs):
        raise PermissionError('revoked')
    monkeypatch.setattr(paper_business_history, '_resolve', revoked)
    assert history.page(slot['resident_id'], ACTOR)['attempts'] == []
    with pytest.raises(PermissionError):
        history.page(slot['resident_id'], ACTOR, input_message_id='msg_' + '3' * 32)


@pytest.mark.parametrize('scope', ['paper:collector','paper:registration-metadata','paper:ccf','paper:summary',
    'paper:deep-read','paper:review','paper:exp','paper:figure','paper:figure-generation'])
def test_all_nine_roles_declare_fresh_and_forward_business_identity(monkeypatch, scope):
    from management_portal import paper_projection as projection, paper_agent_materials
    monkeypatch.setattr(paper_agent_materials, 'TASK_SCOPES', frozenset())
    calls = []
    def method(_name, **options):
        calls.append(options)
        return {'resident_id':'resident_111111111111', 'lineage_id':'lineage_111111111111',
            'message_id':options['message_id'], 'messages':[], 'status':'completed'}
    for attempt in ['attempt-a','attempt-b']:
        projection._call(method, {'actor':'caller','resident_scope':scope,'prompt':'This task only',
            'metadata':{'paper_id':'paper_0123456789ab'}, 'business_ref':attempt})
    a,b=calls
    assert a['workflow']['context_policy'] == b['workflow']['context_policy'] == 'fresh'
    ca,cb=(call['metadata']['business_context'] for call in calls)
    assert ca['subject_ref']==cb['subject_ref']=='paper_0123456789ab'
    assert ca['group_ref']==cb['group_ref'] and ca['attempt_ref']!=cb['attempt_ref']
    assert 'paper:chat' not in projection.ROLES


def test_navigation_is_not_verified_material(owners, monkeypatch):
    _, create = prepare(owners, monkeypatch)
    slot = create('Navigation is not input')
    call = research.isa_client.call
    def owner(actor, instruction, arguments, **kwargs):
        if instruction == 'ResolveMessageContextReferences':
            return {'snapshot': {'type': 'business-history', 'label': 'Not material'}}
        return call(actor, instruction, arguments, **kwargs)
    monkeypatch.setattr(research.isa_client, 'call', owner)
    from management_portal.society_service import resident_protocol
    with pytest.raises(ValueError, match='not a material'):
        resident_protocol.deliver_resident_input({'resident_id': slot['resident_id'], 'message': {
            'message_id': 'msg_' + '8' * 32, 'role': 'user', 'content_parts': [
                {'type': 'text', 'text': 'Input'}, {'type': 'context_reference', 'context': {'structured_reference': {
                    'type': 'business.history', 'version': 1, 'business_context': {}}}}]}}, ACTOR)


@pytest.mark.parametrize('context',[None,{}, {'application':'x'}, {'application':'x','subject_ref':'x','group_ref':'x','attempt_ref':1}])
def test_invalid_business_context(context):
    with pytest.raises(ValueError):
        message_metadata({'business_context':context})


@pytest.mark.parametrize('code', ['instruction.forbidden', 'instruction.not_found', 'instruction.owner_unavailable'])
def test_history_owner_failure_semantics(monkeypatch, code):
    from management_portal.society_service import resident_business_history as history
    from management_portal.hatg.contracts import InstructionError
    seen = []
    def fail(actor, name, arguments, **options):
        seen.append((actor, name, options['timeout']))
        raise InstructionError(code, 'Owner failed')
    monkeypatch.setattr(history.isa_client, 'call', fail)
    with pytest.raises(InstructionError): history._visible('actual-caller', 'resident', {})
    assert seen == [('actual-caller', 'ResolveMessageContextReferences', 10.0)]


def test_same_figure_identity_changed_bytes_is_a_different_material_group(tmp_path):
    from management_portal.paper_business_history import figure_version, build
    image=tmp_path/'figure.png'; image.write_bytes(b'first fixture image')
    figure={'figure_id':'figure-1','caption':'Same caption'}
    original=figure_version(figure,[image])
    assert figure_version(figure,[image])==original
    image.write_bytes(b'corrected fixture image')
    corrected=figure_version(figure,[image])
    assert corrected != original
    contexts=[build(scope='paper:figure',actor='caller',message_id='msg_'+str(i)*32,
        metadata={'paper_id':'paper_fixture','material_version':version}) for i,version in enumerate([original,corrected])]
    assert contexts[0]['subject_ref']==contexts[1]['subject_ref']
    assert contexts[0]['group_ref']!=contexts[1]['group_ref']

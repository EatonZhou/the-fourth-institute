from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal import paper_chat_research as research, paper_reader_service


def test_slots_atomic_selection_account_scope_and_no_creation(monkeypatch):
    monkeypatch.setattr(research.isa_client, 'call', lambda *a, **k: pytest.fail('must not create/read unbound Resident'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: research.reserve_slot(actor='alice', operation_id='one', title='Research'), range(4)))
    assert all(v == values[0] for v in values)
    key = values[0]['slot_id']
    assert research.selected_slot(actor='alice') is None
    research.select_slot(actor='alice', slot_id=key)
    assert research.selected_slot(actor='alice')['slot_id'] == key
    with pytest.raises(KeyError):
        research.select_slot(actor='bob', slot_id=key)
    with pytest.raises(ValueError, match='operation_conflict'):
        research.reserve_slot(actor='alice', operation_id='one', title='Changed')
    other = research.reserve_slot(actor='alice', operation_id='two', title='Independent')
    assert other['slot_id'] != key


def test_index_rechecks_old_acl_versions_without_changing_selected_slot(monkeypatch):
    calls = []
    revoked = set()
    def read(actor, document):
        calls.append((actor, dict(document)))
        if document['paper_id'] in revoked:
            raise PermissionError('revoked')
    monkeypatch.setattr(paper_reader_service, 'production_service', lambda: SimpleNamespace(read=read))
    key = research.reserve_slot(actor='alice', operation_id='material', title='Research')['slot_id']
    research.select_slot(actor='alice', slot_id=key)
    one = {'paper_id': 'paper_000000000001', 'attachment_id': 'attachment-a', 'attachment_version': '1'}
    two = {**one, 'paper_id': 'paper_000000000002'}
    for doc in [one, two, {**one, 'attachment_version': '2'}]:
        research.index_document(actor='alice', slot_id=key, document=doc)
    assert research.selected_slot(actor='alice')['slot_id'] == key
    assert [d['attachment_version'] for d in research.material_index(actor='alice', slot_id=key)['documents']] == ['2', '1']
    revoked.add(one['paper_id'])
    value = research.material_index(actor='alice', slot_id=key)
    assert value['documents'] == [two] and len(value['warnings']) == 1
    research.remove_document(actor='alice', slot_id=key, paper_id=two['paper_id'])
    assert research.material_index(actor='alice', slot_id=key)['documents'] == []
    with pytest.raises(KeyError):
        research.index_document(actor='bob', slot_id=key, document=one)
    assert all(actor == 'alice' for actor, _ in calls)


def test_provision_rejects_shared_lineage_without_creating_or_changing_slot(monkeypatch):
    from management_portal import portal_ui_state, personal_ama
    key = research.reserve_slot(actor='alice', operation_id='private', title='Private')['slot_id']
    fake_store = SimpleNamespace(get=lambda *a: {'value': {'lineage_id': 'lineage_shared', 'resident_id': 'resident_leader'}})
    monkeypatch.setattr(portal_ui_state, 'get_portal_ui_state_store', lambda: fake_store)
    def call(actor, instruction, args, **kw):
        assert instruction == 'GetLineage'
        return {'lineage': {'lineage_membership': 'formal', 'created_by': 'alice'}}
    monkeypatch.setattr(research.isa_client, 'call', call)
    with pytest.raises(PermissionError, match='personal Lineage'):
        research.provision_slot(actor='alice', slot_id=key)
    assert research.get_slot(actor='alice', slot_id=key)['state'] == 'unbound'


def test_adapter_research_identity_stable_across_documents_and_no_code_root(monkeypatch):
    from management_portal import resident_workspace_adapter as adapter
    calls = []
    slot = dict(slot_id='research_'+'a'*24, title='Research', state='bound',
                resident_id='resident_own', lineage_id='lineage_private')
    monkeypatch.setattr(research, 'get_slot', lambda **k: slot)
    monkeypatch.setattr(research, 'material_index', lambda **k: {'documents': [], 'warnings': []})
    monkeypatch.setattr(research, '_document', lambda actor, document: document)
    monkeypatch.setattr(research, 'index_document', lambda **k: calls.append(k))
    for paper in ['paper_000000000001', 'paper_000000000002']:
        context = {'expected_principal': 'alice', 'document': {'paper_id': paper,
            'attachment_id': 'attachment', 'attachment_version': 'v1'}}
        shown = adapter._context_for('paper', 'research:' + slot['slot_id'], '', context,
                                    actor='alice', include_prompt_context=True)
        identity = adapter._ensure('alice', 'paper', 'research:' + slot['slot_id'], shown, create=True)
        assert identity['resident_id'] == 'resident_own'
        assert 'working_directory' not in shown
    assert len(calls) == 2
    with pytest.raises(PermissionError):
        research.workspace_context(actor='bob', slot_id=slot['slot_id'],
            context={'expected_principal': 'alice'}, include_materials=True)


def test_final_resolver_all_materials_or_nothing_and_target_actor(monkeypatch):
    key = research.reserve_slot(actor='alice', operation_id='final', title='Research')['slot_id']
    with research.presentation_store.database().transaction() as db:
        research._put(db, 'alice', research.SLOTS, key, {'state': 'bound', 'resident_id': 'resident_one', 'materials_workspace_id': 'workspace_private'})
    doc = {'paper_id': 'paper_000000000001', 'attachment_id': 'a', 'attachment_version': 'v1'}
    ref = {'type': 'paper.research', 'version': 1, 'slot_id': key, 'expected_principal': 'alice', 'documents': [doc]}
    calls = []
    monkeypatch.setattr(research, '_document', lambda actor, document: calls.append((actor, document)) or document)
    snapshot = research.resolve_reference(actor='alice', target_resident_id='resident_one', reference=ref)
    assert snapshot['documents'] == [doc]
    assert snapshot['workspace_index']['directory'] == 'workspace_private/file/paper-research-materials/indexes/' + key
    assert 'grant no later access' in snapshot['workspace_index']['read_policy']
    for actor, target, changed in [('bob', 'resident_one', {**ref, 'expected_principal': 'bob'}),
                                   ('alice', 'resident_two', ref)]:
        with pytest.raises((KeyError, PermissionError)):
            research.resolve_reference(actor=actor, target_resident_id=target, reference=changed)
    monkeypatch.setattr(research, '_document', lambda *a: (_ for _ in ()).throw(PermissionError('revoked')))
    with pytest.raises(PermissionError):
        research.resolve_reference(actor='alice', target_resident_id='resident_one', reference=ref)
    with pytest.raises(ValueError):
        research.resolve_reference(actor='alice', target_resident_id='resident_one', reference={**ref, 'path': '/private'})


def test_api_does_not_accept_actor_resident_or_stale_principal(monkeypatch):
    monkeypatch.setattr(research, 'provision_slot', lambda **kw: pytest.fail('no provisioning'))
    for payload in [dict(expected_principal='bob', operation_id='a', title='a'),
                    dict(expected_principal='alice', operation_id='a', title='a', actor='bob'),
                    dict(expected_principal='alice', operation_id='a', title='a', resident_id='resident_other')]:
        with pytest.raises((ValueError, PermissionError)):
            research.create_chat(payload, 'alice')


def test_mismatched_ama_leader_has_no_creation_side_effect(monkeypatch):
    from management_portal import portal_ui_state, resident_handoff
    key = research.reserve_slot(actor='alice', operation_id='mismatch', title='Research')['slot_id']
    monkeypatch.setattr(portal_ui_state, 'get_portal_ui_state_store', lambda: SimpleNamespace(get=lambda *a:
        {'value': {'lineage_id': 'lineage_personal', 'resident_id': 'resident_leader'}}))
    calls = []
    def call(actor, instruction, args, **kw):
        calls.append(instruction)
        if instruction == 'GetLineage':
            return {'lineage': {'lineage_membership': 'personal', 'created_by': 'alice'}}
        if instruction == 'GetResident':
            return {'resident': {'resident_id': 'resident_leader', 'lineage_id': 'lineage_other'}}
        pytest.fail('no creation permitted')
    monkeypatch.setattr(research.isa_client, 'call', call)
    monkeypatch.setattr(resident_handoff, 'ensure_invocation_resident', lambda **kw: pytest.fail('no Resident creation'))
    with pytest.raises(PermissionError, match='leader reference'):
        research.provision_slot(actor='alice', slot_id=key)
    assert calls == ['GetLineage', 'GetResident']


def test_legacy_catalog_reads_visible_references_without_creating_or_migrating(monkeypatch):
    from management_portal.workbench_projections import service
    monkeypatch.setattr(research,'selected_slot',lambda **k:None)
    monkeypatch.setattr(research,'list_slots',lambda **k:[])
    monkeypatch.setattr(service,'get_service',lambda:SimpleNamespace(builtin_application=lambda name,actor:{'bindings':{
        'main':{'residents':{'surface-chat:alice:paper:paper:paper_000000000001':'resident_visible',
                             'surface-chat:alice:paper:collection:old':'resident_denied',
                             'surface-chat:bob:paper:paper:paper_000000000002':'resident_bob'}}}}))
    calls=[]
    def call(actor,name,args,**kwargs):
        calls.append((actor,name,args))
        assert actor=='alice' and name=='GetResident'
        if args['resident_id']=='resident_denied':raise PermissionError('revoked')
        assert args['resident_id']=='resident_visible'
        return {'resident':{'title':'Existing history'}}
    monkeypatch.setattr(research.isa_client,'call',call)
    value=research.catalog({},actor='alice')
    assert value['selected'] is None and value['slots']==[]
    assert value['legacy']==[{'workspace_key':'paper:paper_000000000001','title':'Existing history','resident_id':'resident_visible'}]
    assert len(calls)==2

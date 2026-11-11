import pytest
from types import SimpleNamespace
from management_portal import paper_agent_materials as m,paper_projection as projection,paper_store,resources,paper_registration

@pytest.fixture
def owner(monkeypatch,tmp_path):
    monkeypatch.setattr(paper_store,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(resources,'get_resource_service',lambda:SimpleNamespace(get=lambda *a,**kw:{}))
    monkeypatch.setattr(paper_registration,'_paper_registration_draft',lambda id,**kw:{'actor':kw['actor']})
    monkeypatch.setattr(projection.native,'lookup',lambda *a,**kw:{'resident_id':'role-resident'})

@pytest.mark.parametrize('scope',sorted(m.TASK_SCOPES))
def test_material_references_bind_target_and_preserve_workflow_prefix(owner,scope):
    subject='draft:upload-one' if scope=='paper:registration-metadata' else 'paper-one'
    full,ref,verified=m.prepare_task_input(actor='reader',scope=scope,attempt='job-one',subject=subject,full_prompt='Article material and output instructions')
    variants=m.variants_for_message(verified,'Role instructions\n'+full)
    assert variants['path_prompt'].startswith('Role instructions\n')
    assert 'Article material and output instructions' not in variants['path_prompt']
    assert variants['materials'][0]['sha256']==verified['provenance']['sha256']
    with pytest.raises(PermissionError):m.resolve_task_reference(actor='other',target_resident_id='role-resident',reference=ref)
    with pytest.raises(PermissionError):m.resolve_task_reference(actor='reader',target_resident_id='wrong-role',reference=ref)
    again,_,_=m.prepare_task_input(actor='reader',scope=scope,attempt='job-one',subject=subject,full_prompt='new mutable library text')
    assert again==full
    with pytest.raises(ValueError,match='boundary'):m.variants_for_message(verified,full+full)

def test_api_body_remains_complete_and_filename_is_only_server_resolved(owner):
    full,ref,verified=m.prepare_task_input(actor='reader',scope='paper:deep-read',attempt='job-two',subject='paper-one',full_prompt='Full article Section 2 data')
    assert 'Full article Section 2 data' in full
    assert set(ref)=={'type','version','key'}
    assert 'path' not in ref
    with pytest.raises(ValueError):m.resolve_task_reference(actor='reader',target_resident_id='role-resident',reference={**ref,'path':'/etc/passwd'})

@pytest.mark.parametrize('scope',sorted(m.TASK_SCOPES))
def test_projection_sends_owner_reference_and_records_provenance(owner,monkeypatch,scope):
    seen=[]
    monkeypatch.setattr(projection,'_completed_result',lambda result,*a:result)
    def dispatch(name,**options):
        assert 'on_material_prepared' not in options
        reference=options['context_refs']['structured_reference']
        verified=m.resolve_task_reference(actor=options['actor'],target_resident_id='role-resident',reference=reference)
        variants=m.variants_for_message(verified,'Workflow instruction\n'+options['prompt'])
        seen.append((options,variants))
        return {'message':'Source evidence: Section 2. 正文依据完整。','invocation':{},'usage':{}}
    observer=[]
    result=projection._call(dispatch,{'actor':'reader','resident_scope':scope,'prompt':'Article evidence Section 2 text',
        'metadata':{'material_subject':'draft:upload-one' if scope=='paper:registration-metadata' else 'paper-one','material_attempt':'attempt-one'},'on_material_prepared':observer.append})
    assert seen and observer and result['usage']['material_snapshot']==observer[0]
    assert 'Article evidence Section 2 text' in seen[0][0]['prompt']
    assert 'Article evidence Section 2 text' not in seen[0][1]['path_prompt']

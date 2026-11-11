"""Actual Paper/native/handoff wrappers; only external owner I/O is substituted."""
from types import SimpleNamespace
import pytest
from management_portal import paper_projection as projection, resident_handoff as handoff, paper_store, resources, paper_registration
from management_portal.lineage_workflows import WorkflowBindings

class SocietyBoundaryReached(Exception):pass

@pytest.mark.parametrize('scope',['paper:deep-read','paper:review','paper:exp','paper:registration-metadata'])
def test_real_wrappers_carry_material_reference_to_society_boundary(monkeypatch,tmp_path,scope):
    identity={'resident_id':'role-resident','lineage_id':'lineage-test'}
    binding={**identity,'event_source_id':'event-source','effect_id':'effect-test'}
    monkeypatch.setattr(paper_store,'DATA_ROOT',tmp_path)
    monkeypatch.setattr(resources,'get_resource_service',lambda:SimpleNamespace(get=lambda *a,**kw:{}))
    monkeypatch.setattr(paper_registration,'_paper_registration_draft',lambda *a,**kw:{'actor':'reader'})
    monkeypatch.setattr(projection.native,'lookup',lambda *a,**kw:identity)
    monkeypatch.setattr(projection.native,'binding',lambda *a,**kw:identity)
    monkeypatch.setattr(WorkflowBindings,'install',lambda *a,**kw:binding)
    monkeypatch.setattr(handoff,'ensure_invocation_resident',lambda **kw:identity)
    captured=[]
    def society_boundary(self,binding,**kw):
        captured.append(kw)
        raise SocietyBoundaryReached()
    monkeypatch.setattr(WorkflowBindings,'trigger',society_boundary)
    with pytest.raises(SocietyBoundaryReached):
        projection.turn(actor='reader',prompt='Full paper Section 2 evidence',resident_scope=scope,
                        metadata={'material_subject':'draft:upload-one' if scope=='paper:registration-metadata' else 'paper-one','material_attempt':'same-attempt'})
    assert len(captured)==1
    part=captured[0]['content_parts'][0]
    assert part['type']=='context_reference'
    assert part['context']['structured_reference']['type']=='paper.task-material'
    assert 'Full paper Section 2 evidence' in captured[0]['prompt']

@pytest.mark.parametrize('completion',[False,True])
def test_direct_handoff_turn_and_completion_keep_reference(monkeypatch,completion):
    identity={'resident_id':'role','lineage_id':'lineage'}
    workflow={**identity,'event_source_id':'event','effect_id':'effect'}
    monkeypatch.setattr(handoff,'ensure_invocation_resident',lambda **kw:identity)
    seen=[]
    def boundary(self,binding,**kw):
        seen.extend(kw['content_parts']);raise SocietyBoundaryReached()
    monkeypatch.setattr(WorkflowBindings,'trigger',boundary)
    kwargs={'actor':'reader','resident_scope':'paper:test','resident_binding':identity,'workflow_binding':workflow,'context_refs':{'structured_reference':{'type':'test'}}}
    with pytest.raises(SocietyBoundaryReached):
        if completion:handoff.run_resident_completion(user_prompt='body',**kwargs)
        else:handoff.run_resident_turn(prompt='body',cwd='',**kwargs)
    assert seen[0]['context']['structured_reference']=={'type':'test'}

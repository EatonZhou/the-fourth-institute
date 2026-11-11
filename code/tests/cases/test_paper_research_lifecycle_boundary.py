"""Independent projection failure/retry boundary; only failed transport receipt is injected."""
import pytest
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR

@pytest.mark.parametrize('accepted_before_loss',[False,True])
def test_material_projection_failure_retry_keeps_identity(owners,monkeypatch,accepted_before_loss):
    import tempfile
    from management_portal import paper_chat_research as research,portal_ui_state,personal_ama
    from management_portal.society_service import get_society_service
    monkeypatch.setattr(tempfile,'tempdir',str(owners.root))
    monkeypatch.setattr(portal_ui_state.get_portal_ui_state_store(),'key_lock_root',owners.root/'retry-locks')
    personal=get_society_service().lineage.ensure_personal_ama(ACTOR,ACTOR)
    portal_ui_state.get_portal_ui_state_store().put(ACTOR,personal_ama.NAMESPACE,personal_ama.KEY,personal)
    reserved=research.reserve_slot(actor=ACTOR,operation_id='independent-projection-retry',title='Projection retry')
    bound=research.provision_slot(actor=ACTOR,slot_id=reserved['slot_id'])
    real_call=research.isa_client.call
    writes=[];creations=[]
    def transport(actor,instruction,arguments,**kwargs):
        if instruction in {'CreateResident','CreateLineage','DeliverMessage'}:
            creations.append(instruction)
        if instruction=='UpdateWorkspaceFile':
            writes.append(dict(arguments))
            if len(writes)==1:
                if accepted_before_loss:real_call(actor,instruction,arguments,**kwargs)
                raise TimeoutError('injected index write transport loss')
        return real_call(actor,instruction,arguments,**kwargs)
    monkeypatch.setattr(research.isa_client,'call',transport)
    with pytest.raises(TimeoutError):research.project_material_index(actor=ACTOR,slot_id=bound['slot_id'])
    assert research.get_slot(actor=ACTOR,slot_id=bound['slot_id'])==bound
    assert research.provision_slot(actor=ACTOR,slot_id=bound['slot_id'])==bound
    projected=research.project_material_index(actor=ACTOR,slot_id=bound['slot_id'])
    assert writes[0]==writes[1]
    assert projected['address']==writes[0]['address']
    actual=real_call(bound['resident_id'],'ReadResource',{'address':projected['address']})
    assert 'paper.research.material-index.v1' in str(actual)
    with pytest.raises(KeyError):research.get_slot(actor='storage-outsider',slot_id=bound['slot_id'])
    with pytest.raises(KeyError):research.project_material_index(actor='storage-outsider',slot_id=bound['slot_id'])
    assert len(writes)==2 and creations==[]

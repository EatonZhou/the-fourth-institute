"""Deleted reference recovery with disposable real owners; no production deletion."""
import tempfile
import pytest
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR
from tests.cases.test_reader_ai_owner_chain import bind_paper
from management_portal import paper_chat_research as research, portal_ui_state, personal_ama
from management_portal.society_service import get_society_service
from management_portal.hatg.contracts import InstructionError


def prepare(owners, monkeypatch):
    monkeypatch.setattr(tempfile,'tempdir',str(owners.root))
    monkeypatch.setattr(portal_ui_state.get_portal_ui_state_store(),'key_lock_root',owners.root/'ui-locks')
    native=bind_paper(owners,monkeypatch)
    personal=get_society_service().lineage.ensure_personal_ama(ACTOR,ACTOR)
    portal_ui_state.get_portal_ui_state_store().put(ACTOR,personal_ama.NAMESPACE,personal_ama.KEY,personal)
    def slot(name):
        value=research.reserve_slot(actor=ACTOR,operation_id=name,title=name)
        return research.provision_slot(actor=ACTOR,slot_id=value['slot_id'])
    return native,slot


@pytest.mark.parametrize('kind',['current-slot','other-slot','legacy'])
def test_deleted_reference_does_not_break_catalog(owners,monkeypatch,kind):
    native,create=prepare(owners,monkeypatch)
    live=create('Surviving research');dead=create('Deleted research')
    research.select_slot(actor=ACTOR,slot_id=(dead if kind=='current-slot' else live)['slot_id'])
    if kind=='legacy':
        from management_portal import resident_workspace_adapter as adapter
        identity=adapter._workspace_identity(ACTOR,'paper','paper:paper_0123456789ab')
        target=native.ensure('paper',actor=ACTOR,**identity,title='Old discussion',backend='codex',create_role=True)
        research.select_chat({'workspace_key':'paper:paper_0123456789ab','expected_principal':ACTOR},ACTOR)
    else:target=dead
    if kind=='legacy':
        owners.permissions.grant(ACTOR,target['resident_id'],'@admin')
        owners.permissions.grant(ACTOR,target['lineage_id'],'DeleteResident')
    research.isa_client.call(ACTOR,'DeleteResident',{'lineage_id':target['lineage_id'],'resident_id':target['resident_id']},key='delete-fixture-'+kind)
    with pytest.raises(InstructionError) as error:
        research.isa_client.call(ACTOR,'GetResident',{'resident_id':target['resident_id']})
    assert error.value.code=='instruction.not_found'
    for _ in range(2):
        catalog=research.catalog({},actor=ACTOR)
        assert any(item['slot_id']==live['slot_id'] and item['state']=='bound' for item in catalog['slots'])
        assert not any(item['resident_id']==target['resident_id'] for item in catalog['legacy'])
        if kind in ('current-slot','legacy'):
            assert catalog['selected'] is None and catalog['selected_legacy'] is None
        else:assert catalog['selected']['slot_id']==live['slot_id']
    with research.presentation_store.database().read() as connection:
        # Stale references/history aren't deleted, and no repair creates a replacement.
        assert research._get(connection,ACTOR,research.SLOTS,dead['slot_id'])['resident_id']==dead['resident_id']
    assert research.get_slot(actor=ACTOR,slot_id=live['slot_id'])['resident_id']==live['resident_id']


@pytest.mark.parametrize('code',['instruction.forbidden','instruction.owner_unavailable','instruction.failed'])
def test_catalog_never_mistakes_owner_or_permission_failure_for_deletion(owners,monkeypatch,code):
    _,create=prepare(owners,monkeypatch)
    slot=create('Permission intact');research.select_slot(actor=ACTOR,slot_id=slot['slot_id'])
    call=research.isa_client.call
    def fail(actor,instruction,arguments,**kwargs):
        if instruction=='GetResident' and arguments.get('resident_id')==slot['resident_id']:
            raise InstructionError(code,'synthetic owner failure')
        return call(actor,instruction,arguments,**kwargs)
    monkeypatch.setattr(research.isa_client,'call',fail)
    with pytest.raises(InstructionError) as error:research.catalog({},actor=ACTOR)
    assert error.value.code==code
    with research.presentation_store.database().read() as connection:
        assert research._get(connection,ACTOR,research.SELECTION,'active')['slot_id']==slot['slot_id']

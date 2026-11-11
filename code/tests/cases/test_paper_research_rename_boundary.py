"""Rename boundary through real disposable owners and PAR; no model execution."""
import tempfile
import pytest
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR


def test_rename_keeps_identity_history_and_rejects_invalid_authority(owners, monkeypatch):
    from management_portal import paper_chat_research as research, portal_ui_state, personal_ama
    from management_portal.society_service import get_society_service, resident_catalog, resident_runtime_domain
    from tests.cases.permission_test_support import enable
    from management_portal.permission_system.facts import collect_portal_facts
    monkeypatch.setattr(tempfile, 'tempdir', str(owners.root))
    monkeypatch.setattr(portal_ui_state.get_portal_ui_state_store(), 'key_lock_root', owners.root/'rename-locks')
    personal = get_society_service().lineage.ensure_personal_ama(ACTOR, ACTOR)
    portal_ui_state.get_portal_ui_state_store().put(ACTOR, personal_ama.NAMESPACE, personal_ama.KEY, personal)
    reserved = research.reserve_slot(actor=ACTOR, operation_id='rename-boundary', title='Before rename')
    slot = research.provision_slot(actor=ACTOR, slot_id=reserved['slot_id'])
    rid = slot['resident_id']
    permissions = enable(monkeypatch)
    permissions.instance.sync(collect_portal_facts(owners.resource))
    owners.data.display_chat_message(rid, {'message_id':'msg_'+'b'*32, 'role':'user',
        'author_id':ACTOR, 'content_parts':[{'type':'text','text':'Preserved synthetic discussion'}]})
    def current():
        return research.isa_client.call(ACTOR, 'GetResident', {'resident_id':rid})['resident']
    def messages():
        return research.isa_client.call(ACTOR, 'GetConversation', {'resident_id':rid})['messages']
    def cwd():
        return resident_runtime_domain.resident_working_directory(resident_catalog.get_catalog().get(rid))
    before, old_messages, old_cwd = current(), messages(), cwd()
    assert old_messages, 'history assertion must not be vacuous'
    payload = {'expected_principal':ACTOR, 'slot_id':slot['slot_id'], 'title':'  Renamed research  '}
    assert research.rename_chat(payload, ACTOR) == {'resident_id':rid, 'title':'Renamed research'}
    assert research.get_slot(actor=ACTOR, slot_id=slot['slot_id'])['title'] == 'Renamed research'
    assert current()['workspaces'] == before['workspaces']
    assert cwd() == old_cwd and messages() == old_messages
    for title in ['', '   ', 'x'*141, None]:
        with pytest.raises(ValueError): research.rename_chat({**payload,'title':title}, ACTOR)
    for extra in [{'target_resident_id':rid}, {'resident_id':rid}, {'workspace_key':'paper:paper_0123456789ab'}]:
        with pytest.raises(ValueError): research.rename_chat({**payload,**extra}, ACTOR)
    with pytest.raises((KeyError, ValueError)):
        research.rename_chat({**payload,'slot_id':'unknown-slot'}, ACTOR)
    with pytest.raises((KeyError, PermissionError, ValueError)):
        research.rename_chat({**payload,'expected_principal':'storage-outsider'}, 'storage-outsider')
    assert current()['title'] == 'Renamed research'
    # The real UpdateResident owner must reject a viewer who lacks contribution;
    # keep GetResident readable so the rejection cannot be mistaken for an early lookup failure.
    permissions.deny(ACTOR, slot['lineage_id'], 'UpdateResident')
    assert current()['resident_id'] == rid
    from management_portal.hatg.contracts import InstructionError
    with pytest.raises(InstructionError, match='UpdateResident'):
        research.rename_chat({**payload,'title':'Denied rename'}, ACTOR)
    assert current()['title'] == 'Renamed research'
    assert cwd() == old_cwd and messages() == old_messages

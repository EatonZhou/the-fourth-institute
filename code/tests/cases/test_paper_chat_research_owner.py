"""Temporary real owner chain; no provider or external socket access."""
from management_portal import paper_chat_research as research, portal_ui_state, personal_ama
from management_portal.society_service import get_society_service
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR  # noqa: F401


def test_private_research_provisioning_real_owners(owners, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(owners.root))
    monkeypatch.setattr(portal_ui_state.get_portal_ui_state_store(), 'key_lock_root', owners.root / 'ui-locks')
    personal = get_society_service().lineage.ensure_personal_ama(ACTOR, ACTOR)
    portal_ui_state.get_portal_ui_state_store().put(ACTOR, personal_ama.NAMESPACE, personal_ama.KEY, personal)
    slot = research.reserve_slot(actor=ACTOR, operation_id='real-private', title='Private research')
    bound = research.provision_slot(actor=ACTOR, slot_id=slot['slot_id'])
    assert bound['lineage_id'] == personal['lineage_id']
    assert bound['resident_id'] != personal['resident_id']
    assert bound['workspace_id'] != bound['materials_workspace_id']
    assert research.provision_slot(actor=ACTOR, slot_id=slot['slot_id']) == bound

    from management_portal.society_service import resident_execution_domain as domain
    from management_portal.society_service import resident_catalog
    record = resident_catalog.get_catalog().get(bound['resident_id'])
    job = {'actor': ACTOR, 'lineage_id': bound['lineage_id'], 'user_message_id': 'msg_'+'a'*32,
           'execution_id': 'execution-'+'7'*24, 'raw_message': 'Compare authorized materials',
           'actual_sent_prompt': 'Compare authorized materials', 'queue': []}
    request = domain._build_resident_execution_request(record, job)
    public = research.isa_client.call(ACTOR, 'GetResident', {'resident_id': bound['resident_id']})['resident']
    own = next(w for w in public['workspaces'] if w['is_owned'])
    assert request.cwd == own['path']
    assert request.cwd.startswith(str(owners.root))
    assert '/the-fourth-institute' not in request.cwd


def test_private_material_projection_real_par_write_and_resident_read(owners, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(owners.root))
    monkeypatch.setattr(portal_ui_state.get_portal_ui_state_store(), 'key_lock_root', owners.root / 'ui-locks')
    personal = get_society_service().lineage.ensure_personal_ama(ACTOR, ACTOR)
    portal_ui_state.get_portal_ui_state_store().put(ACTOR, personal_ama.NAMESPACE, personal_ama.KEY, personal)
    reserved = research.reserve_slot(actor=ACTOR, operation_id='projection-par', title='Projection research')
    slot = research.provision_slot(actor=ACTOR, slot_id=reserved['slot_id'])
    projection = research.project_material_index(actor=ACTOR, slot_id=slot['slot_id'])
    assert projection['address'].startswith(slot['materials_workspace_id'] + '/file/paper-research-materials/indexes/' + slot['slot_id'] + '/')
    # Actual private-Lineage Resident identity; no temporary grant or alternate
    # Workspace registration, no impersonation of the User principal.
    result = research.isa_client.call(slot['resident_id'], 'ReadResource', {'address': projection['address']})
    assert 'paper.research.material-index.v1' in str(result)

"""Real isolated owner calls: association transfer preserves session and cwd."""
from tests.cases.test_cross_owner_storage_integration import owners, ACTOR
from tests.cases.test_reader_ai_owner_chain import bind_paper, seed_legacy


def test_existing_resident_transfer_keeps_history_and_workspace(owners, monkeypatch):
    import tempfile
    from management_portal import paper_conversations as conversations, portal_ui_state, isa_client
    from management_portal.workbench_projections import http
    monkeypatch.setattr(tempfile, 'tempdir', str(owners.root))
    monkeypatch.setattr(portal_ui_state.get_portal_ui_state_store(), 'key_lock_root', owners.root / 'transfer-locks')
    native = bind_paper(owners, monkeypatch)
    scope = 'surface-chat:' + ACTOR + ':paper:paper:paper_0123456789ab'
    target = seed_legacy(owners, scope)
    owners.data.display_chat_message(target['resident_id'], {'message_id': 'msg_' + 'c' * 32,
        'role': 'user', 'author_id': ACTOR,
        'content_parts': [{'type': 'text', 'text': 'Retained pre-migration discussion'}]})
    history = isa_client.call(ACTOR, 'GetConversation', {'resident_id': target['resident_id']})['messages']
    assert history
    before = isa_client.call(ACTOR, 'GetResident', {'resident_id': target['resident_id']})['resident']
    exported = http.post('/api/workbenches/personal-conversations/export',
        {'scope': scope, 'expected_resident_id': target['resident_id']}, ACTOR)
    assert exported['state'] == 'exported'
    result = http.post('/api/workbenches/personal-conversations/detach',
        {'workbench_id': 'workbench_030303030177', 'scope': scope,
         'expected_resident_id': target['resident_id']}, ACTOR)
    assert result['state'] == 'detached'
    assert native.lookup('paper', actor=ACTOR, resident_scope=scope) == target
    after = isa_client.call(ACTOR, 'GetResident', {'resident_id': target['resident_id']})['resident']
    assert after == before
    assert isa_client.call(ACTOR, 'GetConversation', {'resident_id': target['resident_id']})['messages'] == history

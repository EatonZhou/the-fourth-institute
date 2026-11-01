"""Society creation interruption and ownership regressions on isolated PostgreSQL."""
from pathlib import Path

import pytest

from management_portal.resources.creation import creation_operation
from management_portal.society_service import (
    lineage_lifecycle, lineage_state, lineage_resident_nodes,
    resident_domain, resident_registration_domain, resident_record_domain,
)
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload


def test_lineage_interruption_recovers_original_identity(future_creation_store, monkeypatch):
    attempted = []

    def interrupted(previous, lineage, *args, **kwargs):
        attempted.append(lineage['lineage_id'])
        raise SystemExit('process interrupted after files')

    with creation_operation('lineage-interruption'):
        with monkeypatch.context() as patch:
            patch.setattr(lineage_state, '_persist_lineage_state', interrupted)
            with pytest.raises(SystemExit):
                lineage_lifecycle.create_lineage({'title': 'Recovery'}, 'sample_admin')
        recovered = lineage_lifecycle.create_lineage({'title': 'Recovery'}, 'sample_admin')
    assert recovered['lineage_id'] == attempted[0]


def test_member_creation_refuses_existing_user_directory(future_creation_store, monkeypatch):
    parent = resident_domain.register_resident(_new_session_payload('Parent'), 'sample_admin')
    resident_id = 'resident_0123456789ab'
    target = Path(parent['workspace_path']) / 'residents' / resident_id
    target.mkdir()
    (target / 'resident.md').write_text('user identity')
    (target / 'diary.md').write_text('user diary')
    monkeypatch.setattr(resident_registration_domain, 'unique_resident_id', lambda: resident_id)
    with pytest.raises(FileExistsError):
        lineage_resident_nodes.create_lineage_resident(parent['lineage_id'], _new_session_payload('Member'), 'sample_admin')
    assert sorted(p.name for p in target.iterdir()) == ['diary.md', 'resident.md']
    assert (target / 'resident.md').read_text() == 'user identity'
    assert resident_record_domain.find_resident(resident_id) is None


@pytest.mark.parametrize('member', [False, True])
def test_resident_recovers_published_files_after_transaction_failure(future_creation_store, monkeypatch, member):
    from management_portal.society_service import resident_catalog, state_store, lineage_aggregate
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    parent = resident_domain.register_resident(_new_session_payload('Parent'), 'sample_admin') if member else None
    payload = _new_session_payload('Recover original Resident')
    scope_store = LifecycleStore()
    scope = scope_store.open('creation-test', actor='sample_admin', run_id='run-creation', case_id='recover')
    def create():
        if parent:
            return lineage_resident_nodes.create_lineage_resident(parent['lineage_id'], payload, 'sample_admin')['resident']
        return resident_domain.register_resident(payload, 'sample_admin')
    attempts = []
    original = resident_record_domain.save_resident_record
    def fail(record):
        attempts.append(dict(record))
        original(record)
        raise RuntimeError('database commit interrupted')
    with lifecycle_scope(scope, actor='sample_admin'), creation_operation('resident-crash'):
        with monkeypatch.context() as patch:
            patch.setattr(resident_record_domain, 'save_resident_record', fail)
            with pytest.raises(RuntimeError, match='database commit'):
                create()
        attempted = attempts[0]
        folder = resident_record_domain.resident_folder_path(attempted)
        assert folder.is_dir()
        (folder / 'resident.md').write_text('user edited identity')
        (folder / 'diary.md').write_text('user diary')
        assert scope_store.pending(scope) == []
        assert resident_record_domain.find_resident(attempted['resident_id']) is None
        # Reconstruct the owner and intent stores, retaining only PostgreSQL/files.
        monkeypatch.setattr(state_store, '_STORE', state_store.LineageDataStore())
        monkeypatch.setattr(resident_catalog, '_CATALOGS', {})
        recovered = create()
        assert recovered['resident_id'] == attempted['resident_id']
        assert recovered['lineage_id'] == attempted['lineage_id']
        assert (folder / 'resident.md').read_text() == 'user edited identity'
        assert (folder / 'diary.md').read_text() == 'user diary'
        allocated = set(scope_store.pending(scope))
        assert recovered['resident_id'] in allocated
        assert recovered['owned_workspace_id'] in allocated
        assert (recovered['lineage_id'] in allocated) == (not member)
        assert create()['resident_id'] == recovered['resident_id']


def test_unfinished_creation_cannot_change_scope(future_creation_store, monkeypatch):
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    store = LifecycleStore()
    first = store.open('one', actor='sample_admin', run_id='run-one', case_id='case')
    second = store.open('two', actor='sample_admin', run_id='run-two', case_id='case')
    with lifecycle_scope(first, actor='sample_admin'), creation_operation('scope-owner'):
        with monkeypatch.context() as patch:
            patch.setattr(resident_record_domain, 'save_resident_record', lambda *_: (_ for _ in ()).throw(RuntimeError('stop')))
            with pytest.raises(RuntimeError, match='stop'):
                resident_domain.register_resident(_new_session_payload('Scope'), 'sample_admin')
    with lifecycle_scope(second, actor='sample_admin'), creation_operation('scope-owner'):
        with pytest.raises(ValueError, match='another resource lifecycle'):
            resident_domain.register_resident(_new_session_payload('Scope'), 'sample_admin')


def test_completed_creation_does_not_resurrect_deleted_resident(future_creation_store):
    from tests.cases.test_resident_folder_creation_contract import _delete_member
    with creation_operation('deleted-resident'):
        record = resident_domain.register_resident(_new_session_payload('Deleted'), 'sample_admin')
        _delete_member(record['resident_id'])
        with pytest.raises(KeyError):
            resident_domain.register_resident(_new_session_payload('Deleted'), 'sample_admin')


def test_identical_independent_requests_allocate_independent_identities(future_creation_store):
    payload = _new_session_payload('Same payload')
    first = resident_domain.register_resident(payload, 'sample_admin')
    second = resident_domain.register_resident(payload, 'sample_admin')
    assert first['resident_id'] != second['resident_id']
    assert first['lineage_id'] != second['lineage_id']


def test_rpc_envelope_preserves_creation_identity_scope_and_real_signatures(future_creation_store, monkeypatch):
    import contextvars
    import json
    from types import SimpleNamespace
    from management_common.resource_lifecycle import LifecycleStore, lifecycle_scope
    from management_portal.society_service.server import SocietyServiceServer, _bind_domain_call
    from management_portal.society_service.transport import SocietyServiceClient
    server = SocietyServiceServer.__new__(SocietyServiceServer)
    server.read_pool = SimpleNamespace(workers=0)
    server._finish_changes = lambda _changes: None
    wire = []
    def request(_self, method, params):
        assert method == 'domain.call'
        params = json.loads(json.dumps(params))
        wire.append(params)
        return contextvars.Context().run(server._domain_call, params)
    monkeypatch.setattr(SocietyServiceClient, '_request', request)
    store = LifecycleStore()
    scope = store.open('rpc', actor='sample_admin', run_id='run-rpc', case_id='rpc')
    parent = resident_domain.register_resident(_new_session_payload('RPC parent'), 'sample_admin')
    with lifecycle_scope(scope, actor='sample_admin'), creation_operation('rpc-resident'):
        first = SocietyServiceClient('/unused-test-socket').lineage.create_lineage_resident(parent['lineage_id'], _new_session_payload('RPC'), 'sample_admin')['resident']
        again = SocietyServiceClient('/unused-test-socket').lineage.create_lineage_resident(parent['lineage_id'], _new_session_payload('RPC'), 'sample_admin')['resident']
        projection = SocietyServiceClient('/unused-test-socket').resident.get_resident_resource_projection(first['resident_id'])
        assert projection['resource_id'] == first['resident_id']
    assert first['resident_id'] == again['resident_id']
    assert first['resident_id'] in store.pending(scope)
    assert wire[0]['resource_lifecycle_id'] == scope
    assert wire[0]['creation_operation_id'] == 'rpc-resident'
    assert wire[0]['kwargs'] == {}
    _bind_domain_call('lineage', 'create_lineage_resident',
                      [parent['lineage_id'], _new_session_payload('RPC'), 'sample_admin'], {})
    _bind_domain_call('lineage', 'ensure_personal_ama', ['sample_admin', 'sample_admin'], {})


def test_replaced_published_directory_is_never_adopted_or_cleaned(future_creation_store, monkeypatch):
    from management_portal.society_service import resident_lifecycle_steps_domain
    attempted = []
    def fail(record):
        attempted.append(dict(record))
        raise RuntimeError('commit interrupted')
    payload = _new_session_payload('Replaced')
    with creation_operation('replaced'):
        with monkeypatch.context() as patch:
            patch.setattr(resident_record_domain, 'save_resident_record', fail)
            with pytest.raises(RuntimeError, match='commit interrupted'):
                resident_domain.register_resident(payload, 'sample_admin')
        original = Path(attempted[0]['workspace_path'])
        original.rename(original.with_name(original.name + '-moved-by-user'))
        original.mkdir()
        (original / 'user.txt').write_text('keep')
        with pytest.raises(FileExistsError, match='replaced'):
            resident_domain.register_resident(payload, 'sample_admin')
        operations = resident_record_domain.lifecycle_operations()
        for key in operations.ready():
            operations.run(key, resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
        assert list(original.iterdir()) == [original / 'user.txt']
        assert (original / 'user.txt').read_text() == 'keep'


def test_lineage_empty_position_and_completed_read_preserve_user_files(future_creation_store):
    with creation_operation('lineage-files'):
        lineage = lineage_lifecycle.create_lineage({'title': 'Files'}, 'sample_admin')
        folder = lineage_state._lineage_dir(lineage['lineage_id'])
        (folder / 'LINEAGE.md').write_text('user content')
        replay = lineage_lifecycle.create_lineage({'title': 'Files'}, 'sample_admin')
        assert replay['lineage_id'] == lineage['lineage_id']
        assert (folder / 'LINEAGE.md').read_text() == 'user content'


def test_lineage_refuses_existing_user_directory(future_creation_store, monkeypatch):
    from management_portal.society_service import identity_contract, lineage_storage
    identity = 'lineage_0123456789ab'
    folder = lineage_storage.lineages_root() / identity
    folder.mkdir(parents=True)
    (folder / 'README.md').write_text('user data')
    monkeypatch.setattr(identity_contract, 'creation_candidate_id', lambda *_: identity)
    with pytest.raises(FileExistsError):
        lineage_lifecycle.create_lineage({'title': 'Collision'}, 'sample_admin')
    assert list(folder.iterdir()) == [folder / 'README.md']
    assert (folder / 'README.md').read_text() == 'user data'


def test_fork_recovers_published_files_and_frozen_identity(future_creation_store, monkeypatch):
    from tests.cases.test_resident_folder_creation_contract import _append_conversation_turn
    source = resident_domain.register_resident(_new_session_payload('Fork source'), 'sample_admin')
    _append_conversation_turn(source['resident_id'], 0)
    attempted = []
    save = resident_record_domain.save_resident_record
    def fail(record, **kwargs):
        if record['resident_id'] != source['resident_id']:
            attempted.append(dict(record))
            save(record, **kwargs)
            raise RuntimeError('fork commit interrupted')
        return save(record, **kwargs)
    with creation_operation('fork-recovery'):
        with monkeypatch.context() as patch:
            patch.setattr(resident_record_domain, 'save_resident_record', fail)
            with pytest.raises(RuntimeError, match='fork commit'):
                resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
        folder = resident_record_domain.resident_folder_path(attempted[0])
        (folder / 'resident.md').write_text('edited after interruption')
        recovered = resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')
        assert recovered['resident_id'] == attempted[0]['resident_id']
        assert (folder / 'resident.md').read_text() == 'edited after interruption'
        assert resident_domain.create_resident_full_context_fork(source['resident_id'], 'sample_admin')['resident_id'] == recovered['resident_id']

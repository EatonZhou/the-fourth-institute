"""Controlled quarantine preserves authoritative deletion and rejects reuse."""
import copy
import hashlib
import json

import pytest

from management_portal.resources import registry_store
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.domain_projection_evidence import SOURCE_COLLECTION, EVIDENCE_COLLECTION, tombstone_source

RID = 'resident_123456789abc'
TIME = '2026-10-13T03:58:43Z'


@pytest.fixture
def records(tmp_path, monkeypatch):
    from management_portal.society_service.state_store import LineageDataStore
    state = LineageDataStore()
    state.database.write(lambda c: c.execute(
        'INSERT INTO residents(resident_id,deleted_at,deleted_by) VALUES (%s,%s,%s)',
        (RID, TIME, 'resident_123456789def')))
    store = registry_store.RegistryStore()
    public = dict(schema='tfi.collaboration-descriptor.v2', resource_id=RID,
                  title='Historical title', summary='Keep original text', status='deleted',
                  project_ids=[], relations=[], attributes={}, created_at=TIME, updated_at=TIME, deleted_at=TIME)
    with store.writer('resident', SOURCE_COLLECTION) as writer:
        writer.put('resident', RID, public)
        writer.put(SOURCE_COLLECTION, RID, dict(schema='archive.domain-tombstone-source.v2',
                   resource_id=RID, deleted_at=TIME, source_fingerprint='b' * 64))
    notifications = []
    monkeypatch.setattr(registry_store, 'announce_registry_changes', lambda *a, **kw: notifications.append((a, kw)))
    tmp_path.chmod(0o700)
    return store, state, public, tmp_path / 'archive.json', notifications


def api():
    from management_portal.resources import tombstone_maintenance
    return tombstone_maintenance


def perform(records, expected=None):
    store, _, _, archive, _ = records
    owner = api()
    return owner.quarantine_resident_memo(store, RID,
        expected=expected or owner.inspect_resident_memo(store, RID),
        archive_path=archive, actor='resident_abcdef123456', reason='Original incoming unavailable; invalid memo only')


def test_quarantine_is_archived_atomic_idempotent_and_not_a_recovered_proof(records):
    owner = api()
    store, state, public, archive, notifications = records
    before = owner.inspect_resident_memo(store, RID)
    source_revision = store.revision(SOURCE_COLLECTION)
    result = perform(records, before)
    assert result['changed'] is True
    assert result['proof_recovered'] is False
    assert store.get('resident', RID) == public
    assert store.get(SOURCE_COLLECTION, RID) is None
    assert store.get(EVIDENCE_COLLECTION, RID) is None
    assert store.revision(SOURCE_COLLECTION) > source_revision
    assert owner.inspect_resident_memo(store, RID)['typed'] == before['typed']
    assert json.loads(archive.read_text())['before'] == before
    assert archive.stat().st_mode & 0o777 == 0o600
    assert result['archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert notifications == [((SOURCE_COLLECTION, {RID: 'deleted'}), {}),
                             ((owner.QUARANTINE_COLLECTION, {RID: 'created'}), {})]
    revision = store.revision(SOURCE_COLLECTION)
    assert perform(records, before)['changed'] is False
    assert store.revision(SOURCE_COLLECTION) == revision
    assert len(notifications) == 2
    with store.database.read() as c:
        assert c.execute('SELECT resource_id FROM retired_identities WHERE resource_id=%s', (RID,)).fetchone()


@pytest.mark.parametrize('field', ['source', 'public', 'typed'])
def test_cas_mismatch_has_no_mutations_or_archive(records, field):
    owner = api()
    before = owner.inspect_resident_memo(records[0], RID)
    expected = copy.deepcopy(before)
    expected[field]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='CAS'):
        perform(records, expected)
    assert owner.inspect_resident_memo(records[0], RID) == before
    assert not records[3].exists()
    assert records[4] == []


@pytest.mark.parametrize('case', ['live', 'wrong_time', 'receipt', 'canonical_memo'])
def test_refuses_without_exact_deleted_facts_or_when_proof_is_present(records, case):
    store, state, public, _, _ = records
    if case in ('live', 'wrong_time'):
        from management_portal.resources.workspace_store import WorkspaceStore
        WorkspaceStore().ensure('workspace_123456789abc', str(records[3].parent / 'owned'))
        state.database.write(lambda c: c.execute("UPDATE residents SET owned_workspace_id='workspace_123456789abc',deleted_at=%s WHERE resident_id=%s",
            (None if case == 'live' else '2026-10-14T00:00:00Z', RID)))
    else:
        collection = EVIDENCE_COLLECTION if case == 'receipt' else SOURCE_COLLECTION
        with store.writer(collection) as writer:
            writer.put(collection, RID, {'schema': 'tfi.domain-tombstone-evidence.v2'} if case == 'receipt' else tombstone_source(public))
    before = api().inspect_resident_memo(store, RID)
    with pytest.raises(ValueError):
        perform(records, before)
    assert api().inspect_resident_memo(store, RID) == before
    assert not records[3].exists()


def test_rollback_keeps_memo_fence_revision_and_notifications_unchanged(records, monkeypatch):
    store, _, _, archive, notifications = records
    before = api().inspect_resident_memo(store, RID)
    revision = store.revision(SOURCE_COLLECTION)
    original = registry_store.RegistryWriter.delete
    def fail_after_delete(writer, *args):
        original(writer, *args)
        raise RuntimeError('cancel maintenance')
    monkeypatch.setattr(registry_store.RegistryWriter, 'delete', fail_after_delete)
    with pytest.raises(RuntimeError, match='cancel maintenance'):
        perform(records, before)
    assert archive.exists()  # Archive is durable even if the transaction aborts.
    assert api().inspect_resident_memo(store, RID) == before
    assert store.revision(SOURCE_COLLECTION) == revision
    assert notifications == []


@pytest.mark.parametrize('collection', ['resident', 'lineage', 'family', 'workbench'])
def test_registry_writes_cannot_reuse_fenced_identity(records, collection):
    store = records[0]
    perform(records)
    with pytest.raises(PermissionError, match='retired identity'):
        with store.writer(collection) as writer:
            writer.put(collection, RID, {'status': 'active'})


def test_same_time_retry_without_original_proof_is_rejected_before_and_after(records):
    store, _, public, _, _ = records
    descriptors = CollaborationDescriptorStore(registry=store)
    for before_cleanup in (True, False):
        with pytest.raises(ValueError, match='Stale domain tombstone'):
            with store.writer('resident', SOURCE_COLLECTION, EVIDENCE_COLLECTION) as writer:
                descriptors.tombstone(public, writer, source=tombstone_source(public))
        if before_cleanup:
            perform(records)
    assert store.get('resident', RID) == public
    assert store.get(EVIDENCE_COLLECTION, RID) is None


@pytest.mark.parametrize('entry', ['resident', 'resident_parent', 'lineage', 'family', 'workbench', 'workbench_parent'])
def test_typed_write_entrypoints_reject_retired_identity_and_inheritance(records, entry):
    store, state, _, _, _ = records
    perform(records)
    lid, fid, wid = 'lineage_123456789abc', 'family_123456789abc', 'workbench_123456789abc'
    with store.database.transaction() as c:
        for rid in (lid, fid, wid):
            c.execute('INSERT INTO retired_identities VALUES (%s)', (rid,))
    with pytest.raises(PermissionError, match='retired identity'):
        if entry.startswith('resident'):
            state.put_resident_record(RID if entry == 'resident' else 'resident_abcdef123456',
                {'resident_id': RID if entry == 'resident' else 'resident_abcdef123456',
                 'backend': 'codex', 'lineage_id': lid})
        elif entry == 'lineage':
            state.database.write(lambda c: state._write_definition(c, lid, {'lineage_id': lid}))
        elif entry == 'family':
            from management_portal.society_service import family_storage
            state.database.write(lambda c: family_storage.store_program(c, lid,
                {'function_blocks': [{'id': 'fbi_123456789abc', 'family': {'resource_id': fid, 'title': 'Example'}}]}, TIME))
        else:
            from management_portal.workbench_projections.store import Store
            workbenches = Store()
            payload = {'title': 'Example'}
            if entry == 'workbench_parent':
                wid = 'workbench_abcdef123456'
                payload['bindings'] = {'example': {'lineage_id': 'lineage_abcdef123456', 'residents': {'member': RID}}}
            workbenches.create(wid, payload, 'sample_admin')
    assert store.get('resident', RID) == records[2]


def test_resource_service_rejects_live_projection_and_unauthenticated_retry(records):
    from management_portal.resources.service import ResourceService
    store, _, public, _, _ = records
    perform(records)
    service = ResourceService(collaboration_store=CollaborationDescriptorStore(registry=store))
    with pytest.raises(ValueError, match='Stale domain tombstone'):
        service.sync_collaboration_resource(public)
    live = {**public, 'status': 'active', 'deleted_at': ''}
    with pytest.raises(ValueError, match='Stale canonical domain projection'):
        service.sync_collaboration_resource(live)
    assert store.get('resident', RID) == public
    assert store.get(SOURCE_COLLECTION, RID) is None


def test_concurrent_writer_prevents_retirement_until_whole_operation_can_retry(records):
    import psycopg
    from psycopg.rows import dict_row
    from management_portal.resources.retired_identity_boundary import require_live_identity
    store = records[0]
    expected = api().inspect_resident_memo(store, RID)
    with psycopg.connect(store.database.dsn, row_factory=dict_row) as other:
        require_live_identity(other, RID)
        with pytest.raises(ValueError, match='Concurrent identity writer'):
            perform(records, expected)
        assert not records[3].exists()
    assert perform(records, expected)['changed']


def test_fence_serializes_future_writers_and_notification_waits_for_outer_commit(records):
    import psycopg
    from psycopg.rows import dict_row
    from management_portal.resources.retired_identity_boundary import require_live_identity
    store = records[0]
    expected = api().inspect_resident_memo(store, RID)
    with store.database.transaction():
        perform(records, expected)
        assert records[4] == []
        with psycopg.connect(store.database.dsn, row_factory=dict_row) as other:
            other.execute("SET LOCAL lock_timeout='100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                require_live_identity(other, RID)
            other.rollback()
    with psycopg.connect(store.database.dsn, row_factory=dict_row) as other:
        with pytest.raises(PermissionError, match='retired identity'):
            require_live_identity(other, RID)
    assert len(records[4]) == 2


def test_archive_failure_and_outer_rollback_do_not_publish_any_mutation(records):
    store, _, _, archive, notifications = records
    before = api().inspect_resident_memo(store, RID)
    archive.write_text('existing evidence')
    with pytest.raises(FileExistsError):
        perform(records, before)
    assert archive.read_text() == 'existing evidence'
    assert api().inspect_resident_memo(store, RID) == before
    archive.unlink()
    with pytest.raises(RuntimeError, match='outer abort'):
        with store.database.transaction():
            perform(records, before)
            raise RuntimeError('outer abort')
    assert api().inspect_resident_memo(store, RID) == before
    assert notifications == []

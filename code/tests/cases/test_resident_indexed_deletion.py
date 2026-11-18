"""Identity deletion follows registered ownership, not directory containment.

Real isolated PostgreSQL and the real cleanup worker exercise the transaction
and persisted plan. No Provider or deployed identity is used.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from management_common.directory_cleanup_store import get_directory_cleanup_store
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import resident_catalog, resident_record_domain
from tests.cases.test_resident_folder_creation_contract import (
    future_creation_store, resident_domain, _new_session_payload, _delete_member,
)
from tests.cases.test_hatg_move_resident import teams


def _resident(title):
    return resident_domain.register_resident(_new_session_payload(title), 'sample_admin')


def _tombstone(resident_id, timestamp='2026-11-18T10:30:00Z'):
    return {'resident_id': resident_id, 'deleted_at': timestamp, 'deleted_by': 'sample_admin'}


@pytest.mark.parametrize('entry', ['DeleteResident', 'ApplyLineageChanges'])
def test_nested_self_owned_workspace_is_preserved_and_borrowed_only_unbound(
    future_creation_store, tmp_path, entry,
):
    resident = _resident('Explicit owner')
    rid = resident['resident_id']
    folder = resident_record_domain.resident_folder_path(resident)
    nested = folder / 'independent-workspace'
    nested.mkdir()
    (nested / 'keep').write_text('independent content')
    own = WorkspaceCreationStore().register(
        path=nested, owner_id='', actor='sample_admin', operation_id='independent-nested')
    borrowed = tmp_path / 'borrowed'
    borrowed.mkdir()
    (borrowed / 'keep').write_text('borrowed content')
    binding = WorkspaceCreationStore().register(
        path=borrowed, owner_id='', actor='sample_admin', operation_id='borrowed-workspace')
    resident_domain.add_resident_reference_workspace(rid, str(borrowed), 'sample_admin')
    from tests.cases.isa_test_support import run, reset_kernels
    reset_kernels()
    args = ({'resident_id': rid} if entry == 'DeleteResident' else {
        'operations': [{'op': 'delete', 'kind': 'resident', 'id': rid}]})
    result = run(entry, {'lineage_id': resident['lineage_id'], **args},
                 actor='sample_admin', key='ownership-' + entry)
    assert result.status == 'committed', result
    store = resident_catalog.get_catalog().state_store
    assert store.resident_deletion(rid)['workspace_bindings'] == []
    assert set(get_workspace_store().rows([own['workspace_id'], binding['workspace_id']])) == {
        own['workspace_id'], binding['workspace_id']}
    get_directory_cleanup_store().run_pending()
    assert (nested / 'keep').read_text() == 'independent content'
    assert (borrowed / 'keep').read_text() == 'borrowed content'
    assert not (folder / 'resident.md').exists()


def test_identity_delete_and_worker_do_not_call_generic_tree_discovery(
    future_creation_store, tmp_path, monkeypatch,
):
    resident = _resident('Indexed manifest')
    rid = resident['resident_id']
    external = tmp_path / 'owned-external'
    external.mkdir()
    (external / 'temporary').write_text('private')
    workspace = WorkspaceCreationStore().register(
        path=external, owner_id=rid, actor='sample_admin', operation_id='owned-external')
    from management_portal.resources import directory_registrations

    def forbidden_scan(*args, **kwargs):
        pytest.fail('Resident deletion must consume indexed ownership, not unregister_tree')

    monkeypatch.setattr(directory_registrations, 'unregister_tree', forbidden_scan)
    _delete_member(rid)
    assert not get_workspace_store().rows([workspace['workspace_id']])
    get_directory_cleanup_store().run_pending()
    assert not external.exists()


def test_indexed_plan_and_owned_rows_roll_back_with_identity(future_creation_store, tmp_path):
    resident = _resident('Atomic ownership')
    rid = resident['resident_id']
    external = tmp_path / 'owned-on-rollback'
    external.mkdir()
    wid = WorkspaceCreationStore().register(
        path=external, owner_id=rid, actor='sample_admin', operation_id='owned-rollback')['workspace_id']
    store = resident_catalog.get_catalog().state_store
    with pytest.raises(KeyError):
        store.remove_residents([_tombstone(rid), _tombstone('resident_ffffffffffff')])
    assert not store.resident_record(rid).get('deleted_at')
    assert get_workspace_store().rows([wid])
    assert not get_directory_cleanup_store().status(owner_id=rid)
    assert not get_directory_cleanup_store().status(owner_id=wid)
    assert external.exists()


def test_unchanged_identity_save_does_not_refresh_directory_dependencies(
    future_creation_store, monkeypatch,
):
    from management_portal.society_service import directory_owner, lineage_metadata_storage
    resident = _resident('Ordinary observation')
    rid = resident['resident_id']
    store = resident_catalog.get_catalog().state_store
    record = store.resident_record(rid)
    lineage = store.lineage_definition(record['lineage_id'])

    def forbidden_refresh(*args, **kwargs):
        pytest.fail('A normal identity/metadata save must not enter directory dependency maintenance')

    monkeypatch.setattr(directory_owner, 'record_resident_directory', forbidden_refresh)
    monkeypatch.setattr(directory_owner, 'record_lineage_directory', forbidden_refresh)
    from management_common import directory_dependencies
    monkeypatch.setattr(directory_dependencies, 'prepare_write', forbidden_refresh)
    store.put_resident_record(rid, {**record, 'title': 'Updated observation'})
    with store.database.transaction() as connection:
        lineage_metadata_storage.put(connection, record['lineage_id'], {**lineage, 'description': 'metadata only'})
    assert store.resident_record(rid)['title'] == 'Updated observation'
    assert store.lineage_definition(record['lineage_id'])['description'] == 'metadata only'


def test_move_then_delete_uses_new_canonical_directory_plan(teams):
    from tests.cases.test_hatg_move_resident import execute, request
    from management_portal.society_service import resident_lifecycle_steps_domain
    source, target, member = teams
    rid = member['resident_id']
    old = resident_record_domain.resident_folder_path(member)
    (old / 'private-output').write_text('private state follows ownership')
    moved = execute(request(teams))
    assert moved.status == 'committed', moved
    resident_record_domain.lifecycle_operations().run(
        moved.result['operation_id'], resident_lifecycle_steps_domain.apply_resident_lifecycle_step)
    current = resident_catalog.get_catalog().get(rid)
    new = resident_record_domain.resident_folder_path(current)
    assert current['lineage_id'] == target['lineage_id']
    assert old != new and (new / 'private-output').exists()
    _delete_member(rid)
    get_directory_cleanup_store().run_pending()
    assert not (new / 'private-output').exists()
    assert resident_catalog.get_catalog().get(source['resident_id'])
    assert resident_catalog.get_catalog().get(target['resident_id'])


def test_offline_identity_backfill_uses_qualified_canonical_rows(future_creation_store):
    from management_portal.society_service.directory_owner import migrate_identity_directories
    resident = _resident('Offline directory source')
    store = resident_catalog.get_catalog().state_store
    with store.database.transaction() as connection:
        connection.execute('SET LOCAL search_path TO public')
        result = migrate_identity_directories(connection)
    assert result['residents'] == result['lineages'] == 1
    _delete_member(resident['resident_id'])


def test_concurrent_repeat_deletion_keeps_first_plan(future_creation_store):
    resident = _resident('Repeated ownership')
    rid = resident['resident_id']
    store = resident_catalog.get_catalog().state_store
    timestamp = '2026-11-18T10:30:00Z'
    queue = get_directory_cleanup_store()
    query = (f'SELECT cleanup_id,location,operation_id,preserved_locations FROM {queue.table} '
             'WHERE owner_id=%s ORDER BY cleanup_id')
    started = Event()

    def repeat():
        started.set()
        return store.remove_residents([_tombstone(rid, '2026-11-19T00:00:00Z')])

    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.database.transaction() as connection:
            store.remove_residents([_tombstone(rid, timestamp)])
            # The second writer waits for the same identity row, rather than
            # selecting a new cleanup scope after the first transaction ends.
            future = pool.submit(repeat)
            assert started.wait(timeout=5)
            assert not future.done()
            # Read the uncommitted plan on the external transaction, not the
            # cleanup owner's independent read-only connection.
            first_plan = [dict(row) for row in connection.execute(query, (rid,)).fetchall()]
        future.result(timeout=10)
    assert store.resident_deletion(rid)['deleted_at'] == timestamp
    with store.database.read() as connection:
        assert [dict(row) for row in connection.execute(query, (rid,)).fetchall()] == first_plan
    assert first_plan

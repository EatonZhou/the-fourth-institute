"""Resource performance changes preserve ownership and durable recovery."""
import os
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import pytest

from management_portal import resource_index_store, resource_source_changes
from management_portal.resource_index_records import ChangeSet, resource_record


def item(identity='skill_aaaaaaaaaaaa', **attributes):
    return resource_record(resource_id=identity, resource_type='skill',
                           title='sample', adapter='skill', attributes=attributes)


def test_project_candidates_keep_dynamic_ownership_and_drop_unrelated(monkeypatch):
    store = resource_index_store.IndexStore()
    records = [item(f'skill_{i:012x}', project_ids=['other']) for i in range(100)]
    records.append(item(project_ids=['wanted']))
    dynamic = resource_record(resource_id='workspace_aaaaaaaaaaaa', resource_type='workspace',
                              title='moving', adapter='skill', attributes={'project_ids': ['old']})
    store.apply(ChangeSet('skill', upserts=tuple([*records, dynamic])))
    seen = []
    def project(rows):
        seen.extend(r['resource_id'] for r in rows)
        return [{**r, 'attributes': {**r['attributes'], 'project_ids': ['wanted']}}
                if r['resource_type'] == 'workspace' else r for r in rows]
    monkeypatch.setattr(store, '_project_business_facts', lambda rows: rows)
    monkeypatch.setattr(store, '_filter_private_records', lambda rows, **kw: rows)
    monkeypatch.setattr(resource_index_store, 'readable_flags', lambda rows, actor: [True]*len(rows))
    store.project_changes(['wanted'], records_projector=project, records_projector_types=frozenset({'workspace'}))
    assert set(seen) == {records[-1]['resource_id'], dynamic['resource_id']}


def test_authorization_copy_does_not_hold_index_lock(monkeypatch):
    store = resource_index_store.IndexStore()
    store.apply(ChangeSet('skill', upserts=(item(nested={'values': [1]}),)))
    original = resource_index_store._json_value
    def copy(value):
        acquired = threading.Event()
        def read():
            with store._lock:
                acquired.set()
        thread = threading.Thread(target=read)
        thread.start()
        assert acquired.wait(2), 'deep copy must happen outside the index lock'
        thread.join()
        return original(value)
    monkeypatch.setattr(resource_index_store, '_json_value', copy)
    snapshot = store.authorization_snapshot()
    snapshot['records'][0]['attributes']['nested']['values'].append(2)
    assert store.authorization_snapshot()['records'][0]['attributes']['nested']['values'] == [1]


def test_project_pruning_consumes_domain_projection_contract(monkeypatch):
    from management_portal.society_service import business_fact_views
    store = resource_index_store.IndexStore()
    candidate = item(project_ids=['old'])
    candidate['source']['modified_at'] = '2026-01-01T00:00:00Z'
    store.apply(ChangeSet('skill', upserts=(candidate,)))
    # Simulate the domain owner adding another dynamic type. The query owner
    # must neither drop the candidate nor skip the newly declared projection.
    monkeypatch.setattr(business_fact_views, 'PROJECTED_RESOURCE_TYPES',
                        business_fact_views.PROJECTED_RESOURCE_TYPES | {'skill'})
    seen = []
    def project(rows):
        seen.extend(row['resource_id'] for row in rows)
        return [{**row, 'attributes': {**row['attributes'], 'project_ids': ['wanted']}}
                for row in rows]
    monkeypatch.setattr(business_fact_views, 'project_resources', project)
    monkeypatch.setattr(store, '_filter_private_records', lambda rows, **kw: rows)
    monkeypatch.setattr(resource_index_store, 'readable_flags', lambda rows, actor: [True]*len(rows))
    result = store.project_changes(['wanted'])
    assert seen == ['skill_aaaaaaaaaaaa']
    assert result == {'wanted': '2026-01-01T00:00:00Z'}


@pytest.mark.parametrize('writer', ['direct', 'publish_snapshot', 'reload_database'])
def test_authorization_snapshot_keeps_captured_revision_during_replacement(monkeypatch, writer):
    clock = ['2026-01-01T00:00:00Z']
    monkeypatch.setattr(resource_index_store, '_now_iso', lambda: clock[0])
    store = resource_index_store.IndexStore()
    store.apply(ChangeSet('skill', upserts=(item(nested={'values': [1]}),)))
    old_revision = store.status()['index_updated_at']
    captured = threading.Event()
    resume = threading.Event()
    original = resource_index_store._json_value
    reader_identity = []
    def copy(value):
        if threading.get_ident() in reader_identity and not captured.is_set():
            captured.set()
            assert resume.wait(3)
        return original(value)
    def read():
        reader_identity.append(threading.get_ident())
        return store.authorization_snapshot()
    monkeypatch.setattr(resource_index_store, '_json_value', copy)
    incoming = item(nested={'values': [2]})
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(read)
        try:
            assert captured.wait(3)
            clock[0] = '2026-01-01T00:00:01Z'
            if writer == 'direct':
                store.apply(ChangeSet('skill', upserts=(incoming,)), direct=True)
            elif writer == 'publish_snapshot':
                store.publish_snapshot('skill', [incoming], {})
            else:
                other = resource_index_store.IndexStore()
                other.apply(ChangeSet('skill', upserts=(incoming,)), direct=True)
                store._load_database()
            # Input normalization must also detach the publisher's payload.
            incoming['attributes']['nested']['values'].append(99)
        finally:
            resume.set()
        snapshot = future.result(timeout=3)
    assert snapshot['revision'] == old_revision
    assert snapshot['records'][0]['attributes']['nested']['values'] == [1]
    current = store.authorization_snapshot()
    assert current['revision'] == clock[0] != old_revision
    assert current['records'][0]['attributes']['nested']['values'] == [2]


def test_record_getters_do_not_expose_store_values(monkeypatch):
    from management_portal.resources.query import ResourceAccessPolicy
    store = resource_index_store.IndexStore()
    store.apply(ChangeSet('skill', upserts=(item(nested={'values': [1]}),)))
    monkeypatch.setattr(store, '_filter_private_records', lambda rows, *args, **kw: rows)
    monkeypatch.setattr(resource_index_store, 'readable_flags', lambda rows, actor: [True]*len(rows))
    monkeypatch.setattr(ResourceAccessPolicy, 'readable_ids', lambda self, ids, **kw: set(ids))
    records = [store.internal_get('skill_aaaaaaaaaaaa'),
               store.records_for_adapter('skill')[0], store.get('skill_aaaaaaaaaaaa'),
               store.query(resource_types=['skill'], include_attributes=True)['resources'][0]]
    for record in records:
        record['attributes']['nested']['values'].append(99)
    assert store.authorization_snapshot()['records'][0]['attributes']['nested']['values'] == [1]


def test_unchanged_checkpoint_coalesces_but_restarts_from_durable_cursor(monkeypatch):
    store = resource_index_store.IndexStore()
    store.apply(ChangeSet('skill', upserts=(item(),)))
    clock = [100.0]
    monkeypatch.setattr(resource_index_store.time, 'monotonic', lambda: clock[0])
    store.mark_checked('skill', {'sources_since': 100, 'projection_code_fingerprint': 'a'})
    generation = store.generation()
    for i in range(10):
        clock[0] += 1
        store.mark_checked('skill', {'sources_since': 101+i, 'projection_code_fingerprint': 'a'})
    assert store.generation() == generation
    assert store.adapter_source_state('skill')['sources_since'] == 110
    reopened = resource_index_store.IndexStore()
    assert reopened.adapter_source_state('skill')['sources_since'] == 100
    # An older persisted scan cursor only causes safe extra checking.
    clock[0] = 161
    store.mark_checked('skill', {'sources_since': 161, 'projection_code_fingerprint': 'a'})
    assert store.generation() == generation+1
    store.mark_checked('skill', {'sources_since': 162, 'projection_code_fingerprint': 'b'})
    assert store.generation() == generation+2


def test_checkpoint_retains_resource_publication_lock(monkeypatch):
    store = resource_index_store.IndexStore()
    original = store._connect
    statements = []
    @contextmanager
    def observed():
        with original() as connection:
            class Connection:
                def execute(self, query, parameters=()):
                    statements.append(query)
                    return connection.execute(query, parameters)
                def __getattr__(self, key):
                    return getattr(connection, key)
            yield Connection()
    monkeypatch.setattr(store, '_connect', observed)
    store.apply(ChangeSet('skill', upserts=(item(),)))
    assert any('pg_advisory_xact_lock' in query for query in statements)
    statements.clear()
    store.mark_checked('skill', {'sources_since': 100})
    assert any('pg_advisory_xact_lock' in query for query in statements)
    assert not any('INSERT INTO resource_index_meta' in query for query in statements)
    assert resource_index_store.IndexStore().adapter_source_state('skill')['sources_since'] == 100


def test_source_resolution_shares_parents_without_hiding_symlink_changes(tmp_path, monkeypatch):
    directory = tmp_path/'items'
    directory.mkdir()
    paths = [directory/f'{i}.json' for i in range(50)]
    for path in paths:
        path.touch()
    alias = tmp_path/'alias'
    alias.symlink_to(directory, target_is_directory=True)
    leaf = tmp_path/'leaf'
    leaf.symlink_to(paths[0])
    requested = [str(p) for p in paths] + [str(alias/'0.json'), str(leaf), str(directory/'missing')]
    realpath = os.path.realpath
    expected = {p: realpath(p) for p in requested}
    calls = []
    def counted(path, *args, **kwargs):
        calls.append(path)
        return realpath(path, *args, **kwargs)
    monkeypatch.setattr(resource_source_changes.os.path, 'realpath', counted)
    result = resource_source_changes.find_changes({'since': 0, 'paths': requested})
    assert result['resolved'] == expected
    assert result['missing'] == [str(directory/'missing')]
    assert len(calls) == 3  # 51 sibling leaves, alias parent and symlink leaf
    alias.unlink()
    alias.symlink_to(tmp_path, target_is_directory=True)
    result = resource_source_changes.find_changes({'since': 0, 'paths': [str(alias/'0.json')]})
    assert result['resolved'][str(alias/'0.json')] == str(tmp_path/'0.json')

"""Resource projections copy only needed data while preserving authoritative facts."""
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

from management_common.resource_identity import ResourceTypeRegistry
from management_portal import resource_index_store
from management_portal.resource_index_records import ChangeSet, resource_record
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from management_portal.resources.workspace_store import WorkspaceStore


def record(number):
    return resource_record(resource_id=f'skill_{number:012x}', resource_type='skill',
                           title='test', adapter='skill', path=f'/tmp/{number}',
                           attributes={'nested': {'value': number}})


def test_snapshot_defers_copy_and_preserves_old_generation(monkeypatch):
    store = resource_index_store.IndexStore()
    store.apply(ChangeSet('skill', upserts=tuple(record(i) for i in range(100))))
    original = resource_index_store._json_value
    calls = []
    def copy(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(resource_index_store, '_json_value', copy)
    snapshot = store.adapter_records_snapshot('skill')
    assert len(snapshot) == 100
    assert len(snapshot.source_paths()) == 100
    assert calls == []
    old = snapshot.records_by_id(['skill_000000000001'])
    assert len(calls) == 1
    old['skill_000000000001']['attributes']['nested']['value'] = -1
    assert snapshot[1]['attributes']['nested']['value'] == 1
    snapshot[1]['attributes']['nested']['value'] = -2
    assert snapshot.records_by_id(['skill_000000000001'])['skill_000000000001']['attributes']['nested']['value'] == 1
    replacement = record(1)
    replacement['title'] = 'new'
    store.apply(ChangeSet('skill', upserts=(replacement,), deletes=('skill_000000000002',)))
    assert snapshot[1]['title'] == 'test'
    assert len(list(snapshot)) == 100
    assert len(store.records_for_adapter('skill')) == 99
    assert store.records_for_adapter('skill')[1]['attributes']['nested']['value'] == 1


def test_normalize_reuses_identity_per_batch_without_reusing_descriptors():
    identity = ResourceTypeRegistry()
    calls = []
    class Identity:
        def parse(self, value):
            calls.append(value)
            return identity.parse(value)
    store = CollaborationDescriptorStore(registry=object(), identity=Identity())
    identities = {}
    raw = {'resource_id': 'workspace_000000000001', 'title': 'A',
           'relations': [{'kind': 'owner', 'target_id': 'resident_000000000001'},
                         {'kind': 'member', 'target_id': 'resident_000000000001'}]}
    one = store.normalize(raw, _identities=identities)
    raw['title'] = 'B'
    two = store.normalize(raw, _identities=identities)
    assert one['title'] == 'A' and two['title'] == 'B'
    assert calls == ['workspace_000000000001', 'resident_000000000001']
    store.normalize(raw)
    assert len(calls) == 4  # standalone calls validate fresh


def test_knowledge_source_projection_copies_only_declared_fields(monkeypatch):
    from management_portal.resource_index_knowledge import ProjectKnowledgeAdapter
    store = resource_index_store.IndexStore()
    row = record(1)
    row['attributes'].update(content_root='/tmp/root', canonical_root='/tmp/canonical',
                             unused_payload=['large'] * 1000)
    store.apply(ChangeSet('skill', upserts=(row,)))
    snapshot = store.adapter_records_snapshot('skill')
    original = resource_index_store._json_value
    copied = []
    def copy(value):
        copied.append(value)
        return original(value)
    monkeypatch.setattr(resource_index_store, '_json_value', copy)
    fields = ProjectKnowledgeAdapter.source_resolution_fields
    projected = list(snapshot.projected_records(fields))
    assert set(ProjectKnowledgeAdapter.source_resolution_paths(projected)) == {'/tmp/1', '/tmp/root', '/tmp/canonical'}
    assert copied == ['/tmp/1', '/tmp/root', '/tmp/canonical']
    assert snapshot._copies == {}
    projected[0]['attributes']['content_root'] = 'changed'
    assert snapshot[0]['attributes']['content_root'] == '/tmp/root'


def test_declaration_refresh_is_single_flight(monkeypatch):
    registry = ResourceTypeRegistry()
    registry.parse('workspace_000000000001')
    registry._checked_at = 0
    original = registry._signature
    entered, proceed = threading.Event(), threading.Event()
    calls = []
    def signature():
        calls.append(1)
        entered.set()
        assert proceed.wait(5)
        return original()
    monkeypatch.setattr(registry, '_signature', signature)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(registry.parse, 'workspace_000000000001') for _ in range(8)]
        assert entered.wait(5)
        proceed.set()
        assert all(f.result().resource_type == 'workspace' for f in futures)
    assert len(calls) == 1


def test_no_change_check_never_copies_full_knowledge_records(monkeypatch):
    from management_portal import resource_index_refresh
    from management_portal.resource_index_knowledge import ProjectKnowledgeAdapter
    store = resource_index_store.IndexStore()
    store.apply(ChangeSet('skill', upserts=tuple(record(i) for i in range(100))))
    layer = resource_index_refresh.AdapterLayer(store, [], start_worker=False)
    adapter = ProjectKnowledgeAdapter()
    monkeypatch.setattr(adapter, 'source_scope', lambda: {})
    tracking = {'projection_code_fingerprint': 'same', 'registry_fingerprint': 'same', 'present_files': []}
    monkeypatch.setattr(layer, '_source_tracking_state', lambda *_: tracking)
    monkeypatch.setattr(resource_index_refresh, '_find_source_changes', lambda _: {'changed': [], 'resolved': {}, 'missing': []})
    monkeypatch.setattr(layer, '_unchanged_refresh', lambda *_args, **_kwargs: {'changed': False})
    monkeypatch.setattr(store, 'mark_checked', lambda *_args, **_kwargs: None)
    original = resource_index_store._json_value
    copied = []
    def copy(value):
        copied.append(value)
        return original(value)
    monkeypatch.setattr(resource_index_store, '_json_value', copy)
    result = layer._refresh_changed_sources('skill', adapter, {**tracking, 'sources_since': 1}, 2, (),
                                            started=0, force=False, claimed=False, scan_started_generation=0)
    assert result == {'changed': False}
    assert len(copied) == 100  # source path scalar only, not full records or attributes
    assert all(isinstance(value, str) for value in copied)


def test_workspace_lifecycle_never_locates_paths(monkeypatch):
    from management_portal.resources import workspace_store
    workspace = object.__new__(WorkspaceStore)
    workspace.table = 'test_workspaces'
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [
        {'resource_id': 'workspace_000000000001', 'deleted_at': 'deleted'}]
    @contextmanager
    def read():
        yield connection
    workspace.database = Mock(read=read)
    monkeypatch.setattr(workspace_store, '_located', lambda *_: (_ for _ in ()).throw(AssertionError('path hydrated')))
    assert workspace.lifecycle_facts(['workspace_000000000001'])['workspace_000000000001']['deleted_at'] == 'deleted'
    sql = connection.execute.call_args.args[0]
    assert 'SELECT workspace_id AS resource_id,deleted_at' in sql
    assert '*' not in sql and 'path' not in sql


"""Small committed changes stay independent of the serial reconciliation queue.

:module: resource.service
:design: decision_8dc6b3d03ca7
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import time

import pytest

from management_portal import resource_index_backend as backend
from management_portal.resource_index_registries import CollaborationRegistryAdapter
from management_portal.resources.collaboration_registry import descriptor_resource_record
from management_portal.resources.directory_isa import create_workspace
from management_portal.resources.service import ResourceService
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.test_resource_registration_visibility import knowledge_service, _register
from tests.cases.test_domain_tombstone_and_restore import projection, live_lineage_projection, L


def test_workspace_creation_projects_current_row_without_scan(tmp_path):
    index = backend.ResourceIndexService(store=backend.IndexStore(),
        adapters=[CollaborationRegistryAdapter()], start_worker=False)
    index.adapter_layer._on_projection_changed = None
    service = ResourceService(index_provider=index)
    args = {'path': str(tmp_path / 'created'), 'operation_id': 'small-change', 'title': 'Initial'}
    inv = SimpleNamespace(caller='test-actor', name='CreateWorkspace', argument=lambda key, default=None: args.get(key, default))
    with patch.object(index, 'wait_for_refresh', side_effect=AssertionError('queue wait')), \
         patch.object(index.adapter_layer.adapters['collaboration'], 'scan', side_effect=AssertionError('full scan')):
        started = time.perf_counter()
        result = create_workspace(inv, service)
        print(f'workspace_create_seconds={time.perf_counter() - started:.6f}')
        rid = result['workspace_id']
        assert backend.IndexStore().internal_get(rid)['title'] == 'Initial'
        store = get_workspace_store()
        before = store.descriptor(store.rows([rid])[rid])
        after = store.update_metadata(rid, title='Latest', expected_version=before['updated_at'], authorize=lambda row: None)
        # A late retry with an old payload must publish the current domain row.
        index.project_updated('workspace', descriptor_resource_record(after))
        index.project_updated('workspace', result['resource'])
        assert backend.IndexStore().internal_get(rid)['title'] == 'Latest'


def test_small_update_finishes_during_unrelated_running_scan(knowledge_service):
    service, index = knowledge_service
    record = _register(service)
    entered, release = Event(), Event()
    class SlowAdapter:
        name = 'slow'
        resource_types = frozenset()
        def fingerprint(self):
            return 'slow'
        def scan(self):
            entered.set()
            assert release.wait(5)
            return [], {}
    layer = index.adapter_layer
    layer.adapters['slow'] = SlowAdapter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(layer.refresh_now, 'slow', force=True)
        try:
            assert entered.wait(3)
            started = time.perf_counter()
            changed = pool.submit(service.update, record['resource_id'],
                {'body': '# Fast update'}, actor='test-actor', wait_for_index=True).result(3)
            print(f'update_during_scan_seconds={time.perf_counter() - started:.6f}')
            assert 'index_projection' not in changed
            assert not scan.done()
            assert backend.IndexStore().internal_get(record['resource_id'])['title'] == 'Fast update'
        finally:
            release.set()
        scan.result(3)


def test_collaboration_retry_repairs_failed_projection(projection, monkeypatch):
    from management_portal.resources.errors import ResourceOperationError
    payload = live_lineage_projection(L)
    index = projection.service._index()
    with monkeypatch.context() as broken:
        broken.setattr(index, 'project_updated', lambda *args: (_ for _ in ()).throw(RuntimeError('index unavailable')))
        with pytest.raises(ResourceOperationError) as error:
            projection.service.sync_collaboration_resources([payload])
        assert error.value.details['domain_committed'] is True
    saved = projection.descriptors.get(L)
    projection.service.sync_collaboration_resources([payload])
    assert projection.descriptors.get(L) == saved
    assert backend.IndexStore().internal_get(L)['resource_id'] == L

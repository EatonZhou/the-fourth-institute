"""Cross-service deletion must let its owner publish back into ResourceService.

:module: resource.service
:design: decision_8dc6b3d03ca7
:profiles: local.fixture
:risk: no-external-side-effects
"""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from management_portal import isa_client, resident_handoff
from management_portal.resources.locking import mutation_projection_scope
from management_portal.resources.service import ResourceService
from management_portal.resources.providers import collaboration


class OwnerUnregistration(unittest.TestCase):
    def test_collaboration_owner_can_confirm_tombstone_on_another_rpc_thread(self):
        for kind in ('resident', 'lineage'):
            with self.subTest(kind=kind):
                rid = kind + '_111111111111'
                service = ResourceService(index_provider=SimpleNamespace(),
                                          mutation_provider_loader=collaboration.install_mutation_provider)
                identity = SimpleNamespace(resource_id=rid, resource_type=kind)
                published = threading.Event()
                threads = []
                def publish():
                    with mutation_projection_scope(rid):
                        published.set()
                def owner_delete(**_):
                    thread = threading.Thread(target=publish, daemon=True)
                    threads.append(thread)
                    thread.start()
                    if not published.wait(1):
                        raise RuntimeError('ResourceService locks out the owner tombstone RPC')
                def call(actor, name, args, **_):
                    if name in ('GetResident', 'GetLineage'):
                        return {kind: {'status': 'deleted' if published.is_set() else 'active',
                                       'lineage_id': 'lineage_222222222222'}}
                    if name == 'DeleteLineage':
                        owner_delete()
                        return {}
                    raise AssertionError(name)
                try:
                    with patch.object(service, '_existing_identity', return_value=identity), \
                         patch.object(service, '_require_mutation_actor'), \
                         patch.object(service, '_complete_mutation', side_effect=lambda result, **_: result), \
                         patch.object(isa_client, 'call', side_effect=call), \
                         patch.object(resident_handoff, 'release_resident', side_effect=owner_delete):
                        result = service.unregister(rid, actor='test-actor')
                        self.assertTrue(result['deleted'])
                        self.assertTrue(published.is_set())
                finally:
                    for thread in threads:
                        thread.join(2)
                        self.assertFalse(thread.is_alive())

    def test_local_domain_commit_and_completion_remain_serialized(self):
        rid = 'decision_111111111111'
        service = ResourceService(index_provider=SimpleNamespace())
        identity = SimpleNamespace(resource_id=rid, resource_type='decision')
        published, started = threading.Event(), threading.Event()
        threads = []
        def publish():
            started.set()
            with mutation_projection_scope(rid):
                published.set()
        def unregister(resource_id, **_):
            thread = threading.Thread(target=publish, daemon=True)
            threads.append(thread);thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(published.wait(0.1))
            return {'resource_id': resource_id, 'deleted': True}
        def complete(result, **_):
            self.assertFalse(published.is_set())
            return result
        service.register_mutation_provider('decision', unregister=unregister)
        try:
            with patch.object(service, '_existing_identity', return_value=identity), \
                 patch.object(service, '_ensure_mutation_providers'), \
                 patch.object(service, '_require_mutation_actor'), \
                 patch.object(service, '_complete_mutation', side_effect=complete):
                service.unregister(rid, actor='test-actor')
        finally:
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())
        self.assertTrue(published.is_set())

"""Resource capability inspection reads fresh deployment state in batches.

:module: resource.service
:design: decision_355ed742bca5
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.resources import ResourceService, isa
from management_portal.resources.resource_types import ResourceTypeError


def test_type_listing_batches_state_reads_and_next_call_sees_changes(
    isolated_portal_case, monkeypatch, record_property,
):
    with isolated_portal_case():
        service = ResourceService(
            resolver_loader=lambda service: None,
            mutation_provider_loader=lambda service: None,
            action_provider_loader=lambda service: None,
            index_provider=SimpleNamespace(supports_type=lambda kind: False, type_counts=lambda: {}),
        )
        monkeypatch.setattr(isa, '_service', lambda: service)
        store = service.resource_types.store
        rows = Mock(wraps=store.rows)
        monkeypatch.setattr(store, 'rows', rows)
        first = isa.list_resource_types(None)
        assert len(first['types']) == len(service.resource_types.identity.declarations)
        assert rows.call_count == 2
        record_property('list_resource_types_state_reads', rows.call_count)
        record_property('installed_types', sum(row['installed'] for row in first['types']))
        service.resource_types.update('device', False, expected_version=0, actor='fixture')
        rows.reset_mock()
        second = isa.list_resource_types(None)
        assert rows.call_count == 2
        device = next(row for row in second['types'] if row['type'] == 'device')
        assert device['enabled'] is False and device['version'] == 1
        assert next(row for row in first['types'] if row['type'] == 'device')['version'] == 0
        assert second['registry'] == first['registry']


def test_capabilities_validate_current_installation_after_providers_loaded(
    isolated_portal_case,
):
    with isolated_portal_case():
        service = ResourceService(index_provider=SimpleNamespace(supports_type=lambda kind: False))
        original_units = service.resource_types.units
        service.resource_types.units = frozenset({'core'})
        assert service.capabilities('device')['resource_type'] == 'device'
        service.resource_types.units = frozenset()
        with pytest.raises(ResourceTypeError) as error:
            service.capabilities('device')
        assert error.value.code == 'resource_type.not_installed'
        service.resource_types.units = original_units


def test_batch_capabilities_keep_validation_and_empty_input(isolated_portal_case):
    with isolated_portal_case():
        service = ResourceService(index_provider=SimpleNamespace(supports_type=lambda kind: False))
        assert service.capabilities_many([]) == {}
        with pytest.raises(ValueError, match='unregistered resource type'):
            service.capabilities_many(['does_not_exist'])
        assert service.capabilities_many(['device', 'device']) == {'device': service.capabilities('device')}

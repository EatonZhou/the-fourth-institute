"""Shared write contracts: installed declarations, actual admission and errors."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from management_portal.resources.mutation import MutationProviderRegistry, ResourceMutationProvider
from management_portal.resources.mutation_contracts import (
    prepare_mutation_contract, validate_mutation_payload, ResourceMutationValidationError,
)
from management_portal.resources.service import ResourceService

SCHEMA = {
    'type': 'object', 'properties': {'importance': {
        'type': 'integer', 'minimum': 0, 'maximum': 10, 'description': 'Strict integer priority.',
    }}, 'required': ['importance'], 'additionalProperties': False,
}
LEGACY = {'oneOf': [
    {'type': 'object', 'properties': {'text': {'type': 'string', 'description': 'New content.'}},
     'required': ['text'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'old': {'type': 'string', 'description': 'Original payload.'}},
     'required': ['old'], 'additionalProperties': False, 'x-resource-mode': 'legacy-replay-only'},
]}
ID = 'report_123456789abc'


@pytest.mark.parametrize('value', [True, 1.0, '1', -1, 11])
def test_integer_type_and_range(value):
    with pytest.raises(ResourceMutationValidationError) as error:
        validate_mutation_payload(prepare_mutation_contract(SCHEMA), {'importance': value},
                                  resource_type='report', operation='register')
    assert error.value.details['errors'][0]['path'] == '/importance'
    assert error.value.details['errors'][0]['expected']['type'] == 'integer'
    assert error.value.details['errors'][0]['expected']['maximum'] == 10
    assert isinstance(error.value, ValueError)


def test_payload_not_normalized_and_errors_do_not_echo_submitted_secrets():
    schema = deepcopy(SCHEMA)
    schema['properties']['importance']['default'] = 5
    data = {'importance': 7}
    assert validate_mutation_payload(schema, data, resource_type='report', operation='register') == 'write'
    assert data == {'importance': 7}
    for payload in ({}, {'importance': 'do-not-print-secret'}, {'importance': 1, 'extra': 'secret'}):
        with pytest.raises(ResourceMutationValidationError) as error:
            validate_mutation_payload(schema, payload, resource_type='report', operation='update')
        assert 'secret' not in str(error.value)
        assert 'secret' not in str(error.value.details)
        assert error.value.details['errors'][0]['path'] in ('/importance', '/extra')


def test_local_ref_nested_path_and_replay_selection():
    schema = {'type': 'object', '$defs': {'priority': SCHEMA}, 'properties': {
        'a/b': {'type': 'array', 'items': {'$ref': '#/$defs/priority'}}}}
    prepared = prepare_mutation_contract(schema)
    with pytest.raises(ResourceMutationValidationError) as error:
        validate_mutation_payload(prepared, {'a/b': [{'importance': 12}]}, resource_type='report', operation='update')
    assert error.value.details['errors'][0]['path'] == '/a~1b/0/importance'
    legacy = {'$defs': {'old': LEGACY['oneOf'][1]}, 'oneOf': [LEGACY['oneOf'][0], {'$ref': '#/$defs/old'}]}
    prepared = prepare_mutation_contract(legacy)
    assert validate_mutation_payload(prepared, {'old': 'x'}, resource_type='report', operation='register') == 'legacy-replay-only'
    assert validate_mutation_payload(prepared, {'text': 'x'}, resource_type='report', operation='register') == 'write'
    with pytest.raises(ValueError, match='update'):
        validate_mutation_payload(prepared, {'old': 'x'}, resource_type='report', operation='update')


@pytest.mark.parametrize('schema', [
    {}, {'type': 'object'},
    {'type': 'object', 'properties': {'x': {'$ref': 'https://invalid.example/schema'}}},
    {'type': 'object', 'properties': {'x': {'$ref': '#/$defs/missing'}}},
    {'type': 'object', 'properties': {'x': {'format': 'date'}}},
    {'type': 'object', 'properties': {'x': {'x-resource-mode': 'legacy-replay-only'}}},
    {'type': 'object', 'properties': {}, 'x-resource-mode': 'trust-me'},
    {'type': 'object', 'properties': {'x': {'$ref': '#/$defs/x'}}, '$defs': {'x': {'$ref': '#/$defs/x'}}},
])
def test_bad_declarations_fail_before_installation(schema):
    with pytest.raises((ValueError, TypeError)):
        prepare_mutation_contract(schema)


def test_registry_snapshot_and_atomic_failure():
    registry = MutationProviderRegistry()
    source = deepcopy(SCHEMA)
    provider = ResourceMutationProvider(register=Mock(), register_contract=source)
    registry.register(['report', 'experiment'], provider)
    source['properties']['importance']['maximum'] = 999
    returned = registry.provider_for('report')
    returned.register_contract['properties']['importance']['maximum'] = 999
    assert registry.provider_for('report').register_contract == SCHEMA
    with pytest.raises(ValueError):
        registry.register('report', ResourceMutationProvider(register=Mock()), replace=True)
    assert registry.provider_for('report').register_contract == SCHEMA
    with pytest.raises(ValueError):
        with registry.transaction():
            registry.register('device', provider)
            registry.register('repo', ResourceMutationProvider(update=Mock()))
    assert registry.registered_types() == frozenset({'report', 'experiment'})
    with pytest.raises(ValueError):
        registry.register('repo', ResourceMutationProvider(update_contract=SCHEMA, unregister=Mock()))


@pytest.fixture
def admission(monkeypatch):
    types = SimpleNamespace(require=lambda *a, **kw: None, require_many=lambda *a, **kw: None)
    registrations = Mock()
    registrations.reserve.return_value = (ID, True)
    registrations.existing.return_value = ID
    service = ResourceService(type_registry=types, registration_store=registrations,
                              collaboration_store=object())
    types.identity = service._identity
    complete = Mock(side_effect=lambda result, **kw: dict(result))
    monkeypatch.setattr(service, '_complete_mutation', complete)
    return SimpleNamespace(service=service, registrations=registrations, complete=complete)


@pytest.mark.parametrize('automatic', [False, True])
def test_invalid_register_never_reserves_or_calls_owner(admission, automatic):
    callback = Mock(return_value={'resource_id': ID})
    admission.service.register_mutation_provider('report', register=callback,
        register_contract=SCHEMA, service_managed_registration=True)
    with pytest.raises(ResourceMutationValidationError):
        admission.service.register('report', {'importance': 'secret'}, actor='alice', automatic=automatic)
    callback.assert_not_called()
    admission.registrations.reserve.assert_not_called()
    admission.complete.assert_not_called()
    assert admission.service.register('report', {'importance': 1}, actor='alice', automatic=automatic)['resource_id'] == ID
    callback.assert_called_once()


def test_update_validation_before_content_capture(admission, monkeypatch):
    from management_portal.resources import content_changes
    capture = Mock(side_effect=AssertionError('must validate first'))
    monkeypatch.setattr(content_changes, 'capture_content_changes', capture)
    callback = Mock()
    admission.service.register_mutation_provider('report', update=callback, update_contract=SCHEMA)
    with pytest.raises(ResourceMutationValidationError):
        admission.service.update(ID, {'importance': False}, actor='alice')
    capture.assert_not_called()
    callback.assert_not_called()
    admission.complete.assert_not_called()


def test_legacy_replay_uses_original_key_and_actor_without_allocation(admission):
    callback = Mock(return_value={'resource_id': ID})
    admission.service.register_mutation_provider('report', register=callback,
        register_contract=LEGACY, service_managed_registration=True)
    service = admission.service
    with pytest.raises(ValueError, match='operation_id'):
        service.register('report', {'old': 'x'}, actor='alice')
    admission.registrations.existing.assert_not_called()
    admission.registrations.existing.return_value = None
    with pytest.raises(ValueError, match='existing'):
        service.register('report', {'old': 'x'}, actor='alice', operation_id='original')
    callback.assert_not_called()
    admission.registrations.existing.return_value = ID
    service.register('report', {'old': 'x'}, actor='alice', operation_id='original')
    admission.registrations.existing.assert_called_with('report', {'old': 'x'}, actor='alice', operation_id='original')
    callback.assert_called_with({'old': 'x'}, actor='alice', automatic=False, resource_id=ID)
    admission.registrations.reserve.assert_not_called()
    callback.side_effect = PermissionError('revoked')
    with pytest.raises(PermissionError, match='revoked'):
        service.register('report', {'old': 'x'}, actor='alice', operation_id='original')
    assert admission.complete.call_count == 1


def test_error_roundtrip_keeps_details_and_valueerror():
    from management_portal.resources.errors import resource_error_payload, resource_error_from_payload, resource_error_http_response
    from management_portal.hatg.contracts import normalize_error
    with pytest.raises(ResourceMutationValidationError) as caught:
        validate_mutation_payload(SCHEMA, {'importance': 'private'}, resource_type='report', operation='register')
    error = caught.value
    remote = resource_error_from_payload(resource_error_payload(error))
    assert isinstance(remote, ValueError)
    assert remote.details == error.details
    normalized = normalize_error(remote)
    assert normalized.code == 'instruction.invalid_arguments'
    assert normalized.details == {**error.details, 'reason': 'invalid_argument'}
    assert normalized.commit_state == 'not_committed'
    assert resource_error_http_response(remote)[0] == 400


def test_discovery_returns_exact_copied_contracts(admission, monkeypatch):
    from management_portal.resources import isa
    service = admission.service
    service.register_mutation_provider('report', register=Mock(), register_contract=SCHEMA)
    caps = service.capabilities('report')
    assert caps['contracts'] == {'register': SCHEMA}
    caps['contracts']['register']['properties']['importance']['maximum'] = 999
    assert service.capabilities('report')['contracts']['register'] == SCHEMA
    monkeypatch.setattr(isa, '_service', lambda: service)
    caps = service.capabilities('report')
    projected = isa._resource_type_details('report', row={'installed': True}, counts={}, capabilities=caps)
    assert projected['contracts'] == caps['contracts']
    projected['contracts']['register'].clear()
    assert caps['contracts']['register'] == SCHEMA
    assert isa._resource_type_details('report', row={'installed': False}, counts={})['contracts'] == {}


def test_actual_installed_provider_contracts():
    from management_portal.resources.providers import install_mutation_providers
    from management_portal.resources.resource_types import deployed_units
    service = ResourceService(collaboration_store=object())
    install_mutation_providers(service)
    units = deployed_units()
    for kind, declaration in service._identity.declarations.items():
        if declaration['unit'] not in units:
            assert kind not in service._mutation_providers.registered_types()
            continue
        if kind not in service._mutation_providers.registered_types():
            assert not declaration['registerable'], kind
            continue
        provider = service._mutation_providers.provider_for(kind)
        for operation in ('register', 'update'):
            schema = getattr(provider, operation + '_contract')
            assert (schema is not None) == (getattr(provider, operation) is not None), (kind, operation)
            if schema is not None:
                assert prepare_mutation_contract(schema) == schema

def test_required_false_schema_keeps_structured_error():
    schema = prepare_mutation_contract({
        'type': 'object', 'properties': {'blocked': False}, 'required': ['blocked'],
    })
    with pytest.raises(ResourceMutationValidationError) as caught:
        validate_mutation_payload(schema, {}, resource_type='report', operation='register')
    assert caught.value.details['errors'][0]['path'] == '/blocked'
    assert caught.value.details['errors'][0]['expected'] == {'required': True}


def test_false_subschema_and_finite_json():
    schema = prepare_mutation_contract({'type': 'object', 'properties': {'blocked': False}})
    with pytest.raises(ResourceMutationValidationError) as caught:
        validate_mutation_payload(schema, {'blocked': 'secret'}, resource_type='report', operation='register')
    assert caught.value.details['errors'][0]['path'] == '/blocked'
    assert 'secret' not in str(caught.value.details)
    with pytest.raises(ResourceMutationValidationError):
        validate_mutation_payload(SCHEMA, {'importance': float('nan')}, resource_type='report', operation='register')


def test_isa_merges_envelope_cas_then_uses_owner_contract(admission, monkeypatch):
    from management_portal.resources import isa, content_changes
    from contextlib import contextmanager
    captured = []
    @contextmanager
    def no_execution_capture(**kwargs):
        yield None
    monkeypatch.setattr(content_changes, 'capture_content_changes', no_execution_capture)
    monkeypatch.setattr(isa, '_service', lambda: admission.service)
    schema = {'type': 'object', 'properties': {'expected_version': {'type': 'integer', 'minimum': 1},
              'text': {'type': 'string'}}, 'required': ['expected_version', 'text'], 'additionalProperties': False}
    def update(rid, payload, *, actor):
        captured.append((rid, dict(payload), actor))
        return {'resource_id': rid, 'version': 8}
    admission.service.register_mutation_provider('mailbox_todo', update=update, update_contract=schema)
    rid = 'mailbox_todo_123456789abc'
    inv = SimpleNamespace(argument={'resource_id': rid, 'changes': {'text': 'a', 'expected_version': 3}}.get,
                          expected_version_for=lambda _rid: 7, caller='alice')
    assert isa.update_resource(inv)['version'] == '8'
    assert captured == [(rid, {'text': 'a', 'expected_version': 7}, 'alice')]
    inv.expected_version_for = lambda _rid: True
    with pytest.raises(ResourceMutationValidationError):
        isa.update_resource(inv)
    assert len(captured) == 1


def test_selective_loader_failure_is_not_cached(admission):
    service = admission.service
    class Loader:
        fail = True
        calls = []
        def for_type(self, target, kind):
            self.calls.append(kind)
            target.register_mutation_provider('report', register=Mock(), register_contract=SCHEMA)
            if self.fail:
                target.register_mutation_provider('experiment', update=Mock())
            return {'report'}
    loader = Loader()
    service._mutation_provider_loader = loader
    with pytest.raises(ValueError, match='requires a mutation contract'):
        service._ensure_mutation_providers('report')
    assert service._mutation_providers.registered_types() == frozenset()
    assert not service._loaded_provider_types['mutation']
    loader.fail = False
    service._ensure_mutation_providers('report')
    assert loader.calls == ['report', 'report']


def test_real_registration_ledger_replay_fingerprint_and_no_new_rows(admission):
    from management_portal.resources.registration import ResourceRegistrationStore
    store = ResourceRegistrationStore()
    admission.service._registrations = store
    payload = {'old': 'original'}
    store.reserve('report', payload, actor='alice', operation_id='original',
                  allocate=lambda: ID)
    committed = False
    def owner(data, *, actor, resource_id, **context):
        if not committed:
            raise ValueError('owner has not committed')
        return {'resource_id': resource_id}
    admission.service.register_mutation_provider('report', register=owner,
        register_contract=LEGACY, service_managed_registration=True)
    def rows():
        with store._database.read() as conn:
            return [dict(row) for row in conn.execute(
                'SELECT operation_id,fingerprint,resource_id,completed,deleted FROM resource_registration_operations ORDER BY operation_id')]
    before = rows()
    for actor, operation, data in [('bob', 'original', payload), ('alice', 'new', payload),
                                   ('alice', 'original', {'old': 'changed'}), ('alice', 'original', payload)]:
        with pytest.raises(ValueError):
            admission.service.register('report', data, actor=actor, operation_id=operation)
        assert rows() == before
    committed = True
    assert admission.service.register('report', payload, actor='alice', operation_id='original')['resource_id'] == ID
    assert rows() == before


def test_signed_rpc_contract_and_validation_details(admission, tmp_path, monkeypatch):
    import threading
    from management_portal.resources.server import ResourceServiceServer
    from management_portal.resources.transport import ResourceServiceClient
    from management_portal.resources.config import resource_rpc_signing_key_file
    from management_portal.resources.rpc_auth import load_rpc_signing_key
    from tests.cases.test_resource_service_process import FakePublisher
    service = admission.service
    callback = Mock(return_value={'resource_id': ID})
    service.register_mutation_provider('report', register=callback, register_contract=SCHEMA,
                                      service_managed_registration=True)
    socket_path = tmp_path / 'contracts.sock'
    load_rpc_signing_key(required=True, create=True, path=resource_rpc_signing_key_file(socket_path=socket_path))
    monkeypatch.setattr(ResourceServiceServer, '_start_warmup', lambda self: None)
    monkeypatch.setattr(ResourceServiceServer, '_start_background_maintenance', lambda self: None)
    monkeypatch.setenv('PORTAL_RESOURCE_SERVICE_PROCESS', '1')
    server = ResourceServiceServer(socket_path, service=service, publisher=FakePublisher())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.started.wait(2)
    try:
        client = ResourceServiceClient(socket_path, timeout=3)
        assert client.capabilities('report')['contracts']['register'] == SCHEMA
        with pytest.raises(ValueError) as caught:
            client.register('report', {'importance': 'private'}, actor='alice')
        assert caught.value.details['errors'][0]['path'] == '/importance'
        assert 'private' not in str(caught.value)
        callback.assert_not_called()
        admission.registrations.reserve.assert_not_called()
        client.register('report', {'importance': 2}, actor='alice')
        assert callback.call_args.kwargs['actor'] == 'alice'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)



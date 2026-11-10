import pytest
from management_portal import paper_translation_binding as binding
from management_portal.workbench_projections import native, service
from management_portal.workbench_projections.store import ProjectionError
from tests.cases.test_workbench_projections import store, resources, fake_team, creator, APP, RESIDENT  # noqa: F401


def test_workbench_owner_creates_one_role_and_keeps_fixed_subscription(store, resources, monkeypatch):
    calls = []; nodes = [{'id': RESIDENT}]
    fake_team(monkeypatch, nodes, create=creator(nodes, calls))
    runtime = service.ProjectionService(store)
    runtime.publish(APP, 'alice', store.get(APP, 'alice')['revision'])
    monkeypatch.setattr(runtime, 'builtin_application', lambda name, actor: store.get(APP, actor))
    monkeypatch.setattr(binding, 'get_service', lambda: runtime)
    monkeypatch.setattr(native, 'get_service', lambda: runtime)
    monkeypatch.setattr(native, 'projection_application', lambda name, actor: store.get(APP, actor))
    from management_portal import isa_client
    original = isa_client.call
    def read(actor, instruction, args, **kwargs):
        if instruction == 'GetResident':
            return {'resident': {'provider': 'kimi', 'account': 'kimi-code'}}
        if instruction == 'GetResidentSettings':
            return {'settings': binding.SETTINGS, 'settings_revision': 'fixture-revision'}
        return original(actor, instruction, args, **kwargs)
    monkeypatch.setattr(isa_client, 'call', read)
    assert binding.inspect_binding('alice')['resident_id'] is None
    first = binding.ensure_binding('alice')
    second = binding.ensure_binding('alice')
    assert first['resident_id'] == second['resident_id']
    assert len(calls) == 1 and calls[0]['backend'] == 'kimi'
    assert calls[0]['backend_route_id'] == 'kimi-code'
    assert calls[0]['runtime_settings'] == binding.SETTINGS


def test_draft_main_is_not_silently_published(store, resources, monkeypatch):
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(runtime, 'builtin_application', lambda name, actor: store.get(APP, actor))
    monkeypatch.setattr(binding, 'get_service', lambda: runtime)
    with pytest.raises(ProjectionError): binding.ensure_binding('alice')

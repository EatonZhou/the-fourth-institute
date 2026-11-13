import pytest
from management_portal import paper_translation_binding as binding
from management_portal.workbench_projections import native, service
from management_portal.workbench_projections.store import ProjectionError
from tests.cases.test_workbench_projections import store, resources, fake_team, creator, APP, RESIDENT  # noqa: F401


def test_workbench_owner_creates_one_role_and_keeps_fixed_subscription(store, resources, monkeypatch):
    """Retired subscription translation refuses creation even for a published app."""
    calls = []; nodes = [{'id': RESIDENT}]
    fake_team(monkeypatch, nodes, create=creator(nodes, calls))
    runtime = service.ProjectionService(store)
    runtime.publish(APP, 'alice', store.get(APP, 'alice')['revision'])
    monkeypatch.setattr(runtime, 'builtin_application', lambda name, actor: store.get(APP, actor))
    monkeypatch.setattr(binding, 'get_service', lambda: runtime)
    monkeypatch.setattr(native, 'get_service', lambda: runtime)
    monkeypatch.setattr(native, 'projection_application', lambda name, actor: store.get(APP, actor))
    assert binding.inspect_binding('alice')['resident_id'] is None
    before = store.get(APP, 'alice')
    for _ in range(2):
        with pytest.raises(ProjectionError) as rejected:
            binding.ensure_binding('alice')
        assert rejected.value.code == 'projection.translation_cancelled'
        assert rejected.value.http_status == 410
    assert calls == []
    assert store.get(APP, 'alice') == before


def test_draft_main_is_not_silently_published(store, resources, monkeypatch):
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(runtime, 'builtin_application', lambda name, actor: store.get(APP, actor))
    monkeypatch.setattr(binding, 'get_service', lambda: runtime)
    with pytest.raises(ProjectionError): binding.ensure_binding('alice')

from unittest import mock
from types import SimpleNamespace

from management_portal.resources import ResourceService
from management_portal.resources.providers import workbench
from management_portal.workbench_projections import service, store as storage


def bind_resource_service(store, root, monkeypatch):
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_PROCESS", "1")
    resources = ResourceService(index_provider=mock.Mock())
    monkeypatch.setattr(resources, "notify_source_changed", mock.Mock(return_value={}))
    monkeypatch.setattr(storage, "Store", lambda: store)
    monkeypatch.setattr(service, "Store", lambda: store)
    monkeypatch.setattr(service, "get_resource_service", lambda: resources)
    workbench.install_resolver(resources)
    workbench.install_mutation_provider(resources)
    workbench.install_action_provider(resources)
    return resources


def bind_resident_observation(monkeypatch, execute):
    """Expose fake HATG input fixtures at the shared Resident protocol boundary."""
    from management_portal import resident_handoff

    def read_state(resident_id, actor, message_id):
        return execute(actor, "GetInputState", {"resident_id": resident_id, "message_id": message_id})

    def read_message(resident_id, actor, message_id):
        return {"message_id": message_id, **execute(actor, "GetMessage",
                {"resident_id": resident_id, "message_id": message_id})}

    monkeypatch.setattr(resident_handoff, "get_society_service", lambda: SimpleNamespace(
        resident=SimpleNamespace(get_resident_input_state=read_state, get_resident_message=read_message)))


def use_controlled_projection_account_check(monkeypatch):
    from management_portal.workbench_projections import native, studio

    # This fixture tests callers' handling of a controlled account decision:
    # only "owner" and the TAU account pass. The permission system has its
    # own tests and must not grant this fake visitor.
    def active(actor):
        return actor in {"owner", "test-actor"}

    for module in (service, storage, native, studio):
        if hasattr(module, "is_active_user_or_resident"):
            monkeypatch.setattr(module, "is_active_user_or_resident", active)


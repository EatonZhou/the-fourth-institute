from __future__ import annotations

import json

from .common import canonical_record, explicit_project_ids, registration_project_ids
from ..portal import portal_url_for_address


def install_resolver(service):
    from management_portal.workbench_projections.store import Store

    def resolve(address, *, actor="", **_context):
        resource_id = str(address).split("/", 1)[0]
        app = Store().get(resource_id, actor)
        return {"address": address, "root_id": resource_id, "resource_type": "workbench", "target_kind": "workbench", "record": canonical_record(resource_id, app), "detail": app, "content_kind": "json", "filename": f"{resource_id}.json", "portal_url": portal_url_for_address(resource_id), "exists": True}

    def read(resolved, **_context):
        return json.dumps(resolved["detail"], ensure_ascii=False).encode(), "application/json", {"filename": resolved["filename"]}

    service.register_resolver("workbench", resolve, read_content=read)


def install_mutation_provider(service):
    from management_portal.workbench_projections.store import Store

    def register(payload, *, actor="", resource_id="", automatic=False, **_context):
        store = Store()
        from management_portal.workbench_projections.store import ProjectionError
        from management_portal.workbench_projections.service import ProjectionService
        try:
            app = store.get(resource_id, actor)
        except ProjectionError as error:
            if error.http_status != 404:
                raise
            locate = registration_project_ids if automatic else lambda value: explicit_project_ids(value, "Workbench")
            projects = locate(payload.get("project_ids"))
            ProjectionService(store).check_bindings(payload.get("bindings", {}), actor)
            app = store.create(resource_id, {**payload, "project_ids": projects}, actor)
        return canonical_record(resource_id, app)

    def update(resource_id, payload, *, actor="", **_context):
        from management_portal.workbench_projections.service import ProjectionService
        return canonical_record(resource_id, ProjectionService().apply_update(resource_id, payload, actor))

    def unregister(resource_id, *, actor="", **_context):
        store = Store()
        app = store.get(resource_id, actor)
        store.delete(resource_id, actor)
        return canonical_record(resource_id, {**app, "deleted": True})

    service.register_mutation_provider("workbench", register=register, update=update, unregister=unregister, service_managed_registration=True)


def install_action_provider(service):
    from management_portal.workbench_projections.service import ProjectionService

    def save_generation(resource_id, payload, *, actor="", **_context):
        result = ProjectionService().save_generation_result(resource_id, payload, actor)
        service.notify_source_changed(
            resource_id=resource_id,
            resource_type="workbench",
            kind="updated",
        )
        return result

    def save_reverse_plan(resource_id, payload, *, actor="", **_context):
        return ProjectionService().save_reverse_plan(resource_id, payload, actor)

    service.register_action_provider(
        "workbench",
        {
            "generation.save": save_generation,
            "reverse.plan.save": save_reverse_plan,
        },
    )

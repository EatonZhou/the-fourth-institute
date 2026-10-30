from __future__ import annotations

import logging
from urllib.parse import parse_qs

from management_portal.resources.errors import resource_error_http_response

from .service import get_service
from .store import ProjectionError
from .resource_access import dispatch as resource_request


LOGGER = logging.getLogger(__name__)

def get(path: str, query: dict, actor: str):
    field = lambda name: str((query.get(name) or [""])[0])
    if field('panel') == 'packaging':
        from management_portal.distribution.page import context, state
        if path == '/api/workbenches/native' and field('name') == 'development':
            return {'workbench': context(actor)}
        if path == '/api/workbenches/detail' and field('published') == 'true':
            return context(actor, field('id'))
        if path == '/api/workbenches/state':
            return state(actor, field('id'))
    service = get_service()
    if path == "/api/workbenches":
        return service.catalog(actor, field("lineage_id"))
    if path == "/api/workbenches/detail":
        from .native import with_binding_status

        app = service.store.get(field("id"), actor, published=field("published") == "true")
        try:
            app = with_binding_status(app, actor)
        except Exception:  # noqa: BLE001 - the status is advisory; the page still loads
            LOGGER.warning("Workbench binding status unavailable: %s", field("id"), exc_info=True)
        if field("published") != "true":
            app["latest_generation"] = service.store.latest_generation(field("id"), actor)
        manage = service.store.authorized(app, actor, "manage")
        return {**app, "url": service.summary(app)["url"], "allows": {"UpdateResource": manage}}
    if path == "/api/workbenches/generation":
        return service.generation(field("id"), actor)
    if path == "/api/workbenches/state":
        return service.store.state(field("id"), actor, panel=field("panel"))
    if path == "/api/workbenches/native":
        app = service.store.builtin(field("name"), actor)
        manage = bool(app) and service.store.authorized(app, actor, "manage")
        return {"workbench": {**service.summary(app), "allows": {"UpdateResource": manage}} if app else None}
    if path == "/api/workbenches/activation-report":
        from .native import activation_report
        app = service.store.get(field("id"), actor)
        service.store.require_write(app, actor)
        return activation_report(app, actor)
    raise ProjectionError("projection.route", "工作台接口不存在", 404)


def post(path: str, payload: dict, actor: str):
    if path == "/api/workbenches/release":
        from management_portal.distribution.api import dispatch
        if payload.get("panel") != "packaging":
            raise ValueError("Release operations belong to the packaging panel")
        return dispatch(payload.get("operation"), payload.get("arguments") or {}, actor)
    if path == '/api/workbenches/state' and payload.get('panel') == 'packaging':
        from management_portal.distribution.page import state
        return state(actor, str(payload.get('id') or ''), payload.get('value', {}))
    service = get_service()
    app_id = str(payload.get("id") or "")
    if path == "/api/workbenches/create":
        return service.create(payload, actor)
    if path == "/api/workbenches/update":
        return service.update(app_id, {key: value for key, value in payload.items() if key != "id"}, actor)
    if path == "/api/workbenches/publish":
        return service.publish(app_id, actor, int(payload["expected_revision"]))
    if path == "/api/workbenches/delete":
        return service.delete(app_id, actor)
    if path == "/api/workbenches/initialize":
        return service.initialize(str(payload.get("name") or ""), actor)
    if path == "/api/workbenches/favorite":
        return service.set_favorite(app_id, payload.get("favorite") is True, actor)
    if path == "/api/workbenches/generate":
        return service.start_generation(payload, actor)
    if path == "/api/workbenches/reverse":
        from .studio import start_reverse
        return start_reverse(service, payload, actor)
    if path == "/api/workbenches/reverse/apply":
        from .studio import apply_reverse
        return apply_reverse(service, payload, actor)
    if path == "/api/workbenches/reverse/plan":
        from .studio import revise_plan
        return revise_plan(service, payload, actor)
    if path == "/api/workbenches/isa":
        return service.dispatch(app_id, payload, actor)
    if path in {"/api/workbenches/object", "/api/workbenches/observe", "/api/workbenches/facade"}:
        app = service.store.get(app_id, actor, published=not bool(payload.get("preview")))
        if path.endswith("/object"):
            from .semantics import semantic_object
            return semantic_object(payload.get("object"), actor)
        if path.endswith("/observe"):
            from .semantics import observe
            return observe(payload.get("refs"), actor)
        chat = (app.get("manifest") or {}).get("chat")
        panel = str(payload.get("panel") or "")
        if panel:
            region = next((item for item in (app.get("manifest") or {}).get("sidebar", []) if item["id"] == panel), None)
            if region is None:
                raise ProjectionError("projection.panel_unknown", "功能区不存在", 404)
            chat = region.get("chat", chat)
        if not chat:
            raise ProjectionError("projection.resident_unbound", "工作台没有绑定门面对话", 409)
        binding = app["bindings"].get(chat["binding"], {})
        service.check_bindings({chat["binding"]: binding}, actor)
        return {"resident_id": binding["residents"]["facade"], "lineage_id": binding["lineage_id"]}
    if path == "/api/workbenches/state":
        return service.store.state(app_id, actor, payload.get("value", {}), panel=str(payload.get("panel") or ""))
    if path == "/api/workbenches/directories":
        from .host_directories import browse
        service.store.get(app_id, actor)
        result = browse(payload.get("arguments") or {}, actor)
        service.store.get(app_id, actor)
        return result
    if path == "/api/workbenches/resource":
        from management_portal.resources import get_resource_service
        service.store.get(app_id, actor)
        return resource_request(
            get_resource_service(), payload.get("operation"), payload.get("arguments") or {}, actor,
            check_workbench=lambda: service.store.get(app_id, actor),
        )
    raise ProjectionError("projection.route", "工作台接口不存在", 404)


def handle(handler, parsed, actor: str, *, write: bool = False):
    from management_portal.http_request_body import _read_bounded_json_object
    from management_portal.http_responses import _serve_json
    try:
        from .application_http import PREFIX, LIBRARY_PREFIX, serve_asset, serve_library
        from management_portal.distribution.page import PublishedPackaging
        if not write and parsed.path.startswith('/api/workbenches/release-assets/'):
            from management_portal.distribution.page import serve_asset as serve_release_asset
            return serve_release_asset(handler, parsed.path, actor)
        if not write and parsed.path.startswith('/api/workbenches/release-libraries/'):
            return serve_library(handler, parsed.path.replace('/release-libraries/', '/libraries/', 1), actor, PublishedPackaging())
        if not write and parsed.path.startswith(PREFIX):
            return serve_asset(handler, parsed.path, actor, get_service().store)
        if not write and parsed.path.startswith(LIBRARY_PREFIX):
            return serve_library(handler, parsed.path, actor, get_service().store)
        result = post(parsed.path, _read_bounded_json_object(handler, max_bytes=524_288), actor) if write else get(parsed.path, parse_qs(parsed.query), actor)
        return _serve_json(handler, 200, result)
    except Exception as error:
        from management_portal.hatg.contracts import InstructionError
        from management_portal.http_errors import instruction_error_payload

        if isinstance(error, InstructionError):
            status, payload = error.http_status, instruction_error_payload(error)
        else:
            status, payload = resource_error_http_response(error)
        if status >= 500:
            LOGGER.exception("Workbench route failed: %s", parsed.path)
        return _serve_json(handler, status, payload)

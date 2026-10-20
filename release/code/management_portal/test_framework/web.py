"""Test Lab catalog, source, demo, and execution HTTP routes."""

from pathlib import Path
from urllib.parse import parse_qs

from management_common.distribution_features import included
from management_common.operation_errors import OperationError
from management_portal.http_routes import RouteDefinition
from management_portal.test_framework import api as test_framework_api
from management_portal.test_framework import live_adapter as test_live_adapter

# The Test Workbench page scripts live with the framework, not in management_portal/static.
PAGE_ROOT = Path(__file__).resolve().parent / "page"
PAGE_ASSETS = (
    "test_case_panel.js", "test_catalog_panel.js", "test_gate_composer_panel.js", "test_live_adapter.js",
    "test_review_panel.js", "test_run_console_panel.js", "test_tau_panel.js", "test_workbench.css",
    "test_workbench.js", "test_workbench_core.js",
)
# The Test workbench's view in the development projection page (web.native_view).
NATIVE_VIEW = {
    "id": "test", "label": "Test", "icon": "shield-check", "title": "测试目录、运行、TAU 与审查",
    "template": PAGE_ROOT / "test_native_view.html",
}


class TestLabHttp:
    def __init__(self, *, respond, exception_response, serve_bytes, unavailable_response, read_json):
        self.respond = respond
        self.exception_response = exception_response
        self.serve_bytes = serve_bytes
        self.unavailable_response = unavailable_response
        self.read_json = read_json

    def catalog_case(self, request, parsed, route_actor):
        qs = parse_qs(parsed.query)
        case_id = str((qs.get('case_id') or [''])[0]).strip()
        if not case_id:
            return self.respond(request, 400, {'error': 'case_id is required'})
        try:
            return self.respond(request, 200, {'case': test_framework_api.case_definition(case_id)})
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def _catalog_filters(self, parsed):
        qs = parse_qs(parsed.query)
        filters = {'search': str((qs.get('search') or [''])[0]).strip(), 'importance': qs.get('importance') or [], 'modules': qs.get('module') or [], 'features': qs.get('feature') or [], 'aspects': qs.get('aspect') or [], 'lifecycle': qs.get('lifecycle') or [], 'design_point_ref': str((qs.get('design_point_ref') or [''])[0]).strip()}
        design_value = str((qs.get('is_design_point') or [''])[0]).strip().lower()
        if design_value in {'true', 'false'}:
            filters['is_design_point'] = design_value == 'true'
        live_value = str((qs.get('has_live') or [''])[0]).strip().lower()
        if live_value in {'true', 'false'}:
            filters['has_live'] = live_value == 'true'
        binding_value = str((qs.get('has_design_point_ref') or [''])[0]).strip().lower()
        if binding_value in {'true', 'false'}:
            filters['has_design_point_ref'] = binding_value == 'true'
        return filters

    def catalog_query(self, request, parsed, route_actor):
        try:
            return self.respond(request, 200, test_framework_api.query_case_ids(self._catalog_filters(parsed)))
        except Exception as exc:
            return self.exception_response(request, exc)

    def catalog(self, request, parsed, route_actor):
        try:
            qs = parse_qs(parsed.query)
            filters = self._catalog_filters(parsed)
            projection = str((qs.get('projection') or [''])[0]).strip().lower()
            result = test_framework_api.query_workspace(filters, actor=route_actor) if projection == 'workspace' else test_framework_api.query(filters, actor=route_actor)
            result['access'] = request._test_api_access
            return self.respond(request, 200, result)
        except Exception as exc:
            return self.exception_response(request, exc)

    def source(self, request, parsed, route_actor):
        qs = parse_qs(parsed.query)
        case_id = str((qs.get('case_id') or [''])[0]).strip()
        implementation_id = str((qs.get('implementation_id') or [''])[0]).strip()
        try:
            return self.respond(request, 200, test_framework_api.implementation_source(case_id, implementation_id, actor=route_actor))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (FileNotFoundError, UnicodeError, ValueError) as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def demo(self, request, parsed, route_actor):
        qs = parse_qs(parsed.query)
        case_id = str((qs.get('case_id') or [''])[0]).strip()
        try:
            return self.respond(request, 200, test_live_adapter.demo_descriptor(case_id))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def demo_frame(self, request, parsed, route_actor):
        qs = parse_qs(parsed.query)
        case_id = str((qs.get('case_id') or [''])[0]).strip()
        session = str((qs.get('session') or [''])[0]).strip()
        try:
            return self.serve_bytes(request, 200, test_live_adapter.demo_frame_html(case_id, session), 'text/html; charset=utf-8', extra_headers={'Content-Security-Policy': "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'none'; form-action 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'", 'Referrer-Policy': 'no-referrer'})
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def tau_residents(self, request, parsed, route_actor):
        try:
            return self.respond(request, 200, test_framework_api.tau_resident_status(parse_qs(parsed.query).get('module') or []))
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception:
            return self.unavailable_response(request)

    def tau_admission(self, request, parsed, route_actor):
        try:
            return self.respond(request, 200, test_framework_api.tau_admission(actor=route_actor))
        except Exception as exc:
            return self.exception_response(request, exc)

    def tau_admission_update(self, request, parsed, route_actor):
        return self._post(request, route_actor, lambda payload, actor: test_framework_api.set_tau_admission(
            payload.get("paused"), payload.get("expected_revision"), actor=actor))

    def run(self, request, parsed, route_actor):
        qs = parse_qs(parsed.query)
        actor = route_actor
        run_id = str((qs.get('run_id') or [''])[0]).strip()
        try:
            return self.respond(request, 200, test_framework_api.get_run(run_id, actor=actor))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def _post(self, request, actor, action):
        try:
            payload = self.read_json(request)
            return self.respond(request, 200, action(payload, actor))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except OperationError as exc:
            return self.exception_response(request, exc)
        except Exception as exc:
            return self.exception_response(request, exc)

    def preview(self, request, parsed, actor):
        return self._post(request, actor, lambda payload, user: test_framework_api.preview(payload, actor=user))

    def run_start(self, request, parsed, actor):
        def start(payload, user):
            run_request = payload
            case_id = str(payload.get('case_id') or '').strip()
            if case_id and not isinstance(payload.get('select'), dict):
                run_request = {'select': {'case_ids': [case_id]}, 'implementation': {}}
            return test_framework_api.start(run_request, actor=user)
        return self._post(request, actor, start)

    def run_cancel(self, request, parsed, actor):
        return self._post(request, actor, lambda payload, user: test_framework_api.cancel_run(str(payload.get('run_id') or '').strip(), actor=user))

    def runs_delete(self, request, parsed, actor):
        def delete(payload, user):
            run_ids = payload.get('run_ids')
            if not isinstance(run_ids, list):
                raise ValueError('run_ids 必须是测试运行 ID 数组')
            return test_framework_api.delete_runs(run_ids, actor=user)
        return self._post(request, actor, delete)

    def tau_rotate(self, request, parsed, actor):
        return self._post(request, actor, lambda payload, user: test_framework_api.rotate_tau_resident(str(payload.get('module_id') or '').strip(), actor=user, backend_route_id=str(payload.get('backend_route_id') or '').strip()))

    def tau_configure(self, request, parsed, actor):
        return self._post(request, actor, lambda payload, user: test_framework_api.configure_tau_residents(payload.get('module_ids'), actor=user, backend_route_id=str(payload.get('backend_route_id') or ''), fast=payload.get('fast')))


def routes(portal):
    """The Test Workbench scripts and API (``web.routes``); ``portal`` is the Web shell.

    ``/test/api/`` callers pass the shell's agent-token or session check first.
    """
    http = TestLabHttp(
        respond=lambda *args, **kwargs: portal._serve_json(*args, **kwargs),
        exception_response=lambda request, exc: portal._serve_exception_json(request, exc),
        serve_bytes=lambda *args, **kwargs: portal._serve_bytes(*args, **kwargs),
        unavailable_response=lambda request: portal._serve_unavailable_json(request),
        read_json=lambda request: portal._read_bounded_json_object(request),
    )

    def asset(request, parsed, actor):
        name = Path(parsed.path).name
        content_type = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
        return portal._serve_static_file(request, PAGE_ROOT / name, content_type)

    api = (
        ('GET', 'catalog', http.catalog), ('GET', 'catalog/case', http.catalog_case),
        ('GET', 'catalog/query', http.catalog_query), ('GET', 'source', http.source),
        ('GET', 'demo', http.demo), ('GET', 'demo-frame', http.demo_frame),
        ('GET', 'tau/residents', http.tau_residents), ('GET', 'run', http.run),
        ('GET', 'tau/admission', http.tau_admission),
        ('POST', 'preview', http.preview), ('POST', 'run', http.run_start),
        ('POST', 'run/cancel', http.run_cancel), ('POST', 'runs/delete', http.runs_delete),
        ('POST', 'tau/resident/rotate', http.tau_rotate), ('POST', 'tau/resident/configure', http.tau_configure),
        ('POST', 'tau/admission', http.tau_admission_update),
    )
    return (
        *(RouteDefinition('GET', f'/static/{name}', asset, 'portal-public-get', phase='public')
          for name in (PAGE_ASSETS if included('test_view') else ())),
        *(RouteDefinition(method, f'/test/api/{path}', handler, f'test-actor-{method.lower()}')
          for method, path, handler in api),
    )

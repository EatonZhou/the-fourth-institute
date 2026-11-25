"""The workshop UI exercises the real template, assistant and workspace-file instructions."""
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.cases.isa_test_support import ok
from tests.cases.test_isa_lineage_lifecycle import lineage as lineage  # noqa: F401
from tests.cases.test_isa_lineage_lifecycle import future_creation_store as future_creation_store  # noqa: F401


ROOT = Path(__file__).resolve().parents[2]


def test_workshop_draft_semantics():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/cases/javascript/template_workshop.cjs'),
                             str(ROOT / 'tests/cases/javascript/duty_workshop_history.cjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_workshop_browser_with_real_instructions(lineage):
    from management_portal.script_groups import expand_document
    from management_portal.society_service import principal_hierarchy_domain, lineage_reads
    without_personal = lineage_reads.list_lineages('sample_admin')
    assert not any(row['lineage_membership'] == 'personal' for row in without_personal['lineages'])
    personal = principal_hierarchy_domain.ensure_personal_ama('sample_admin','sample_admin')
    principal_hierarchy_domain.ensure_personal_assistant('sample_admin',personal['resident_id'],'sample_admin')
    before = ok('GetLineage', {'lineage_id':personal['lineage_id']})['lineage']
    family_before = next(block['family'] for block in before['function_blocks'] if block.get('family',{}).get('personal_ama_id'))

    html = expand_document((ROOT / "management_portal/template_workshop.html").read_text())
    fragment = (ROOT / 'management_portal/static/template_workshop_panel.html').read_text().replace('<div class="templateWorkshop">','<div class="templateWorkshop" id="templateWorkshop">',1)
    html = html.replace('<main id="workshopPage" class="workshopStandaloneHost"></main>',f'<main class="workshopStandaloneHost workshopPanelHost">{fragment}</main>')
    # This harness keeps production graph, editor, dialogs and Chat, while calls
    # use the isolated test stores instead of a signed-in production session.
    import re
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    groups = json.loads((ROOT / 'management_portal/script_groups.json').read_text())
    modules = list(dict.fromkeys(groups['portal-foundation'] + groups['portal-shell'] + [
        '/static/portal_command_registry.js', '/static/portal_workbench_context.js', '/static/portal_editor_open_service.js',
        *groups['portal-workbench'], '/static/resident_protocol.js',
        '/static/resident_projection.js', '/static/portal_resident_runtime_controller.js',
        '/static/portal_resource_routes.js', '/static/portal_resource_chips.js',
        '/static/vendor/monaco/vs/nls/lang/zh-cn.js', '/static/vendor/monaco/vs/loader.js',
        '/static/portal_code_editor.js',
        *groups['chat-workspace'], '/static/portal_hatg_loader.js',
        '/static/lineage_template_workshop_draft.js', '/static/template_workshop_surface.js', '/static/lineage_template_workshop.js',
        '/static/lineage_duty_workshop_model.js', '/static/lineage_duty_workshop.js',
        '/static/lineage_template_workshop_chat.js',
        '/static/portal_template_workshop_loader.js',
        '/static/lineage_workbench_editors.js',
        '/static/portal_resident_service.js',
    ]))
    html = html.replace('</head>', ''.join(f'<script src="{src}"></script>' for src in modules) + '</head>')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            from management_portal.resources.portal import portal_route_script
            path = self.path.split('?', 1)[0]
            if path == '/':
                content, media = html.encode(), 'text/html'
            elif path == '/static/portal_resource_routes.js':
                content, media = portal_route_script(), 'text/javascript'
            elif path == '/lineage/api/lineages':
                content, media = json.dumps(lineage_reads.list_lineages('sample_admin')).encode(), 'application/json'
            elif path == '/without-personal':
                content, media = json.dumps(without_personal).encode(), 'application/json'
            elif path == '/api/agent-conversations':
                from urllib.parse import parse_qs, urlsplit
                resident_id = parse_qs(urlsplit(self.path).query).get('resident', [''])[0]
                if resident_id:
                    ok('GetResident', {'resident_id':resident_id})
                content, media = json.dumps({'entries':[], 'target_state':'present' if resident_id else ''}).encode(), 'application/json'
            elif path == '/api/agent-workbench/residents/projection':
                from urllib.parse import parse_qs, urlsplit
                from management_portal import resident_chat_projection
                from management_portal.society_service.resident_domain import get_resident_session_projection
                rid = parse_qs(urlsplit(self.path).query)['id'][0]
                value = resident_chat_projection.project_session(get_resident_session_projection(rid,'sample_admin'),'sample_admin')
                content, media = json.dumps(value).encode(), 'application/json'
            elif path.startswith('/static/'):
                file = ROOT / 'management_portal' / path.lstrip('/')
                if not file.is_file():
                    self.send_error(404)
                    return
                content = file.read_bytes()
                media = 'text/css' if file.suffix == '.css' else 'text/javascript'
            else:
                content, media = b'{}', 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', media)
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            call = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                result = ok(call['instruction'], call.get('arguments'),
                            key=call.get('options', {}).get('idempotencyKey', ''),
                            version=call.get('options', {}).get('expectedVersion'))
                response = {'result': result}
            except Exception as error:
                response = {'error': {'message': str(error), 'code': getattr(error, 'code', 'instruction.failed')}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(['node', str(ROOT / 'tests/cases/javascript/template_workshop.browser.cjs')],
            cwd=ROOT, env={**os.environ, 'WORKSHOP_TEST_ORIGIN': f'http://127.0.0.1:{server.server_port}',
                           'WORKSHOP_TEST_LINEAGE': lineage, 'WORKSHOP_PERSONAL_LINEAGE':personal['lineage_id'],
                           'WORKSHOP_PERSONAL_ASSISTANT':personal['resident_id']}, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        # The actual personal-entry retry and Family reconcile leave the helper
        # outside that Family: no implicit canvas-wide membership discovery.
        from management_portal.society_service import lineage_graph_edits
        from management_portal.society_service.templates.familize import reconcile_in_body
        principal_hierarchy_domain.ensure_personal_assistant('sample_admin',personal['resident_id'],'sample_admin')
        def sync(body):
            for block in body['function_blocks']:
                if block.get('family',{}).get('personal_ama_id'):
                    reconcile_in_body(body,block,'sample_admin')
        after, _ = lineage_graph_edits.commit_lineage_edit(personal['lineage_id'],'sample_admin',sync,reason='Verify workshop isolation')
        family_after = next(block['family'] for block in after['function_blocks'] if block.get('family',{}).get('personal_ama_id'))
        assert family_after['members'] == family_before['members']
        helper = next(block for block in after['function_blocks'] if block.get('template_id') == 'template_080f09d6226a')
        helper_ids = {element['id'] for element in helper['elements'] if element['kind'] == 'resident'}
        assert helper_ids and not helper_ids.intersection([*family_after['members'],family_after['facade'],family_after['steward'],family_after['router']])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

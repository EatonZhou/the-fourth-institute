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
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/cases/javascript/template_workshop.cjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_workshop_browser_with_real_instructions(lineage):
    from management_portal.script_groups import expand_document

    html = expand_document((ROOT / "management_portal/template_workshop.html").read_text())
    # This harness keeps production graph, editor, dialogs and Chat, while calls
    # use the isolated test stores instead of a signed-in production session.
    import re
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    groups = json.loads((ROOT / 'management_portal/script_groups.json').read_text())
    modules = list(dict.fromkeys(groups['portal-foundation'] + groups['portal-shell'] + [
        '/static/portal_command_registry.js', '/static/portal_workbench_context.js',
        *groups['portal-workbench'], '/static/resident_protocol.js',
        '/static/resident_projection.js', '/static/portal_resident_runtime_controller.js',
        '/static/portal_resource_routes.js', '/static/portal_resource_chips.js',
        '/static/vendor/monaco/vs/nls/lang/zh-cn.js', '/static/vendor/monaco/vs/loader.js',
        '/static/portal_code_editor.js',
        *groups['chat-workspace'], '/static/portal_hatg_loader.js',
        '/static/lineage_template_workshop_draft.js', '/static/lineage_template_workshop.js',
        '/static/lineage_template_workshop_chat.js',
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
                           'WORKSHOP_TEST_LINEAGE': lineage}, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

"""Chromium uses production UI modules with isolated, real ISA requests."""
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.cases.isa_test_support import ok
from tests.cases.test_isa_lineage_lifecycle import lineage as lineage, future_creation_store as future_creation_store  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def test_store_chromium(lineage):
    page = (ROOT / 'management_portal/template_workshop.html').read_text()
    page = re.sub(r'<script\b[^>]*>.*?</script>', '', page, flags=re.S)
    groups = json.loads((ROOT / 'management_portal/script_groups.json').read_text())
    scripts = list(dict.fromkeys(groups['portal-foundation'] + groups['portal-shell'] + [
        '/static/portal_hatg_loader.js', '/static/lineage_template_workshop_draft.js', '/static/template_workshop_surface.js', '/static/lineage_template_workshop.js']))
    page = page.replace('</head>', ''.join(f'<script src="{src}"></script>' for src in scripts) + '</head>')
    hatg = (ROOT / 'management_portal/hatg-panel.html').read_text()
    workshop = (ROOT / 'management_portal/static/template_workshop_panel.html').read_text()
    page = page.replace('<main id="workshopPage" class="workshopStandaloneHost"></main>',
                        f'<div id="testHatg" class="portalHatgPanelComponent" style="width:420px;height:850px;position:relative">{hatg}</div><div id="testWorkshop" style="width:940px;height:850px">{workshop}</div>')
    output = Path(os.environ.get('TEMPLATE_STORE_SCREENSHOTS', '/tmp/template-store-screenshots'))
    output.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            if path == '/':
                data, media = page.encode(), 'text/html'
            elif path == '/static/portal_resource_routes.js':
                from management_portal.resources.portal import portal_route_script
                data, media = portal_route_script(), 'text/javascript'
            elif path.startswith('/static/'):
                file = ROOT / 'management_portal' / path.lstrip('/')
                if not file.is_file():
                    self.send_error(404)
                    return
                data, media = file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'text/plain'
            else:
                data, media = b'{}', 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', media)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            call = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                response = {'result':ok(call['instruction'],call.get('arguments'),actor=call.get('actor','sample_admin'))}
            except Exception as error:
                response = {'error':{'message':str(error)}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        result = subprocess.run(['node',str(ROOT / 'tests/cases/javascript/template_store.browser.cjs')],cwd=ROOT,capture_output=True,text=True,timeout=90,
                                env={**os.environ,'STORE_TEST_ORIGIN':f'http://127.0.0.1:{server.server_port}','STORE_TEST_LINEAGE':lineage,'STORE_SCREENSHOTS':str(output)})
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

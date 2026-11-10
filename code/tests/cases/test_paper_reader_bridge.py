import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from management_portal import paper_reader_annotations as module
from tests.cases.test_paper_reader_annotations import batch, selector


def test_transport_real_pg_bridge():
    bound_context = object()
    def authenticate(context):
        assert context is bound_context
        return 'bridge-authenticated-a'
    def resolve(context, value):
        assert context is bound_context and value == selector()
        return module.Attachment(**selector())
    store = module.AnnotationStore(authenticate=authenticate, resolve_attachment=resolve)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            length = int(self.headers['Content-Length'])
            assert length <= module.MAX_BYTES
            payload = json.loads(self.rfile.read(length))
            try:
                method = {'/read': store.read, '/commit': store.commit}[self.path]
                result = method(bound_context, payload)
                status = 200
            except (ValueError, PermissionError) as exc:
                result, status = {'error': str(exc)}, getattr(exc, 'status', 400)
            self.send_response(status); self.end_headers()
            self.wfile.write(json.dumps(result).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        script = Path(__file__).parent / 'javascript/zotero_reader_pg_bridge.mjs'
        result = subprocess.run(['node', str(script), f'http://127.0.0.1:{server.server_port}'], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PASS real PG bridge' in result.stdout
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


def test_geometry_and_read_limit(monkeypatch):
    store = module.AnnotationStore(authenticate=lambda c: 'geometry-test', resolve_attachment=lambda c, v: module.Attachment(**v))
    good = batch()
    good['upserts'][0]['position']['rects'] = [[-5, -4, 20, 30]]
    store.commit(None, good)  # Crop-relative negative coordinates are valid.
    for position in [dict(pageIndex=0, rects=[[4, 3, 2, 5]]),
                     dict(pageIndex=0, rects=[[0, 1, True, 4]]),
                     dict(pageIndex=0, rects=[]),
                     dict(pageIndex=0, rects=[[0, 0, 1, 1]], nextPageRects=[[0, 0, 1, 1]]),
                     dict(pageIndex=0, rects=[[0, 0, 1, 1]], rotation=45),
                     dict(pageIndex=0, rects=[[0, 0, 1, 1]], unsupported=1)]:
        bad = batch('bad', '1'); bad['upserts'][0]['position'] = position
        with pytest.raises(ValueError): store.commit(None, bad)
    monkeypatch.setattr(module, 'MAX_READ_ROWS', 0)
    with pytest.raises(ValueError, match='read limit'): store.read(None, selector())
    monkeypatch.setattr(module, 'MAX_READ_ROWS', 4096)
    monkeypatch.setattr(module, 'MAX_READ_BYTES', 1)
    with pytest.raises(ValueError, match='read limit'): store.read(None, selector())

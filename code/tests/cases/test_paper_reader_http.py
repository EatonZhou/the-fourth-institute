import json
import subprocess
import threading
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from management_portal.http_paper_reader import routes
from management_portal.paper_reader_service import ReaderService


def large_seekable_pdf():
    # The final page's content is between two large streams. Neither stream is
    # needed to render a page; fetching its body would show up in the byte count.
    page_count = 12
    page_ids = list(range(4, 4 + page_count))
    content_ids = list(range(4 + page_count, 4 + 2 * page_count))
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               ('<< /Type /Pages /Kids [' + ' '.join(f'{i} 0 R' for i in page_ids)
                + f'] /Count {page_count} >>').encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for content_id in content_ids:
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources '
                       f'<< /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>'.encode())
    def stream(data):
        return f'<< /Length {len(data)} >>\nstream\n'.encode() + data + b'\nendstream'
    for page in range(1, page_count + 1):
        objects.append(stream(f'BT /F1 18 Tf 60 700 Td (Range test page {page}) Tj ET'.encode()))
    objects.extend([stream(b'x' * (4 * 1024**2))] * 2)
    order = [*range(1, content_ids[-1]), len(objects) - 1, content_ids[-1], len(objects)]
    data = bytearray(b'%PDF-1.7\n')
    offsets = {}
    for i in order:
        offsets[i] = len(data)
        data.extend(f'{i} 0 obj\n'.encode() + objects[i - 1] + b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for i in range(1, len(objects) + 1):
        data.extend(f'{offsets[i]:010} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return data


def test_reader_http_request_validation():
    from types import SimpleNamespace
    def call(headers, payload):
        headers = {'Content-Type': 'application/json', **headers}
        definitions = routes(service=None, get_auth=lambda r: object(), authenticate=lambda auth: 'a',
            read_json=lambda r: payload, respond=lambda r, status, body: status)
        return definitions[0].handler(SimpleNamespace(headers=headers), urlsplit('/paper/api/reader/open'), '')
    assert call({'X-Reader-Principal': 'b'}, {}) == 409
    assert call({'X-Reader-Principal': 'a', 'Content-Length': '300000'}, {}) == 413
    assert call({'X-Reader-Principal': 'a'}, []) == 400
    assert call({'X-Reader-Principal': 'a'}, {'paper_id': 'p', 'source': '/client/path'}) == 400


def test_production_reader_assembly_is_lazy(monkeypatch):
    from management_portal import paper_store
    from management_portal.http_paper_reader import production_routes
    def forbidden(*args): raise AssertionError('route assembly must not read/import Papers')
    monkeypatch.setattr(paper_store, '_paper_by_id', forbidden)
    definitions = production_routes(object())
    assert len(definitions) == 5
    assert all(d.phase == 'dual-auth' for d in definitions[:4])
    assert definitions[4].phase == 'protected'


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_http_native_reader(tmp_path, monkeypatch, browser):
    root = Path(__file__).resolve().parents[2]
    static = root / 'management_portal/paper_reader_app/zotero_prototype'
    bundle = tmp_path / 'bundle'; bundle.mkdir()
    source = bundle / 'paper.pdf'
    fixture = root / 'tests/cases/fixtures/paper-synthetic-means.pdf'
    source.write_bytes(large_seekable_pdf())
    deleted = set()
    revoked = set()
    def authorize(actor, paper):
        if actor not in ('a', 'b') or actor in revoked or paper != 'paper_fixture' or paper in deleted: raise PermissionError()
    service = ReaderService(locate=lambda paper: (bundle, source), authorize=authorize, deleted=lambda p: p in deleted)
    # Interrupt after immutable publication; the next explicit open must resume the intent.
    from management_portal.paper_reader_attachments import AttachmentOwner
    original = AttachmentOwner._recover
    once = [True]
    def interrupted(owner, context, operation):
        result = original(owner, context, operation)
        if once[0]:
            once[0] = False
            raise RuntimeError('accepted import; source-pointer not yet committed')
        return result
    monkeypatch.setattr(AttachmentOwner, '_recover', interrupted)
    import pytest
    with pytest.raises(RuntimeError): service.open('a', 'paper_fixture')
    monkeypatch.setattr(AttachmentOwner, '_recover', original)
    assert service.open('a', 'paper_fixture')['document']
    assert service.open('b', 'paper_fixture')['annotations'] == []
    def respond(request, status, body):
        request.send_response(status); request.send_header('Content-Type', 'application/json'); request.end_headers(); request.wfile.write(json.dumps(body).encode())
    definitions = routes(service=service, get_auth=lambda request: request.headers.get('Cookie'),
        authenticate=lambda auth: {'session=a': 'a', 'session=b': 'b'}.get(auth, ''),
        read_json=lambda request: json.loads(request.rfile.read(int(request.headers['Content-Length']))), respond=respond)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self): self.dispatch()
        def do_GET(self): self.dispatch()
        def dispatch(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/test/revoke':
                revoked.add('a')
                return respond(self, 200, {})
            if parsed.path == '/test/replace':
                source.write_bytes(fixture.read_bytes() + b'\n% new version\n')
                return respond(self, 200, {})
            for definition in definitions:
                if definition.matches(self.command, parsed.path, 'dual-auth'):
                    return definition.handler(self, parsed, '')
            if parsed.path == '/':
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
                self.wfile.write(b'''<iframe id="frame" src="/upstream/reader.html" style="width:100%;height:900px"></iframe><script type="module">
import {openHttpDocument} from '/http_transport.js';import {mountReader} from '/host.js';
window.ready=true;window.openDocument=async()=>{window.transport=await openHttpDocument({paperId:'paper_fixture',principal:'a'});window.host=await mountReader(document.querySelector('iframe'),{...window.transport.options,status:message=>window.readerError=message});};
</script>'''); return
            file = (static / parsed.path.lstrip('/')).resolve()
            if not file.is_relative_to(static) or not file.is_file(): self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', {'.js':'text/javascript','.mjs':'text/javascript','.html':'text/html','.css':'text/css','.wasm':'application/wasm'}.get(file.suffix,'application/octet-stream')); self.end_headers(); self.wfile.write(file.read_bytes())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = subprocess.run(['node', str(root/'tests/cases/javascript/zotero_reader_http.cjs'), f'http://127.0.0.1:{server.server_port}', browser], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        print(result.stdout.strip())
        revoked.clear()
        # Formal deletion ordering, with a failed first attempt recoverable by durable intent.
        service.prepare_delete('paper_fixture')
        from management_portal.paper_reader_annotations import Conflict
        with pytest.raises(Conflict): service.finish_delete('paper_fixture')
        with pytest.raises(Conflict): service.open('a', 'paper_fixture')
        deleted.add('paper_fixture')
        service.finish_delete('paper_fixture'); service.finish_delete('paper_fixture')
        with service.database.read() as db:
            assert db.execute('SELECT count(*) FROM reader_annotations').fetchone()[0] == 0
        assert source.exists()  # physical bundle deletion is owned by the existing formal cleanup queue
    finally:
        server.shutdown(); server.server_close(); thread.join(5)

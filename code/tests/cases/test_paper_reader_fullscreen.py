import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from tests.cases.test_paper_reader_hooks import setup_paper
from tests.cases.test_portal_security_repair_wave1 import _configure_portal_session
from management_portal import http_paper, web_accounts, http_auth
from management_portal.portal_access_control import PortalAccessStore


def test_translation_client_task_binding():
    script = Path(__file__).parent / 'javascript/paper_translation_client.mjs'
    subprocess.run(['node', str(script)], check=True, capture_output=True, timeout=5)


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_formal_fullscreen(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'zotero_reader_fullscreen.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_same_reader_layout(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'zotero_reader_same_layout.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_page_recent(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'zotero_reader_recent.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_translation_ui(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'zotero_reader_translation.cjs')


@pytest.mark.parametrize('browser', ['chromium', 'firefox'])
def test_translation_owner_ui(tmp_path, monkeypatch, browser):
    run_host(tmp_path, monkeypatch, browser, 'zotero_reader_translation_owner.cjs', translation=True)


def run_host(tmp_path, monkeypatch, browser, script, *, translation=False):
    paper, bundle, store = setup_paper(tmp_path, monkeypatch)
    access = PortalAccessStore()
    from management_portal import personal_ama
    monkeypatch.setattr(personal_ama, 'provision_account', lambda *a, **k: None)
    for user in ('reader_one', 'reader_two'): access.upsert_user(user, actor='CASE')
    _configure_portal_session(tmp_path, monkeypatch, access)
    tokens = [web_accounts._issue_session_token(u) for u in ('reader_one', 'reader_two')]
    google_calls = []
    if translation:
        import io
        from management_portal import paper_translation_service
        from management_portal.paper_translation_google import GoogleTransport
        monkeypatch.setattr(paper_translation_service, '_production', None)
        owner = paper_translation_service.production_service()
        class Response(io.BytesIO):
            status = 200
        def opener(request, **kwargs):
            google_calls.append(request.full_url)
            return Response(json.dumps([[['正式隔离译文', 'synthetic']]]).encode())
        owner.google = GoogleTransport(opener=opener)
        # Real route/owner/cache/transport; only the external Google HTTP I/O is synthetic.
        monkeypatch.setattr(owner.execution, 'submit', lambda *a, **k: pytest.fail('unexpected model request'))
    definitions = http_paper.routes(object())
    from management_portal import paper_recent
    monkeypatch.setattr(paper_recent, 'get_resource_service', lambda: type('R', (), {'get': lambda *a, **k: {'resource_id': paper}})())
    monkeypatch.setattr(paper_recent, 'resource_index_papers_by_ids', lambda ids, **k: [{'id': i, 'title': i} for i in ids])
    root = Path(__file__).resolve().parents[2]
    (bundle / 'paper.pdf').write_bytes((root/'tests/cases/fixtures/paper-synthetic-means.pdf').read_bytes())
    from management_portal.resources.directory_registrations import register_owned
    second = tmp_path / 'second'; second.mkdir()
    (second / 'paper.pdf').write_bytes((bundle / 'paper.pdf').read_bytes())
    (second / 'manifest.json').write_text(json.dumps({'paper_id':'paper_abcdef012345','title':'Second','files':{'pdf':'paper.pdf'}}))
    register_owned('paper', 'paper_abcdef012345', [second])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self): self.dispatch()
        def do_GET(self): self.dispatch()
        def dispatch(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/integration':
                self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers()
                self.wfile.write((root/'tests/cases/fixtures/paper-reader-recent.html').read_bytes()); return
            if parsed.path == '/':
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
                self.wfile.write(('''<script type="module">import {openReader} from '/static/paper_fullscreen_reader.js';
window.signals=[];window.openDocument=(embedded=false)=>{window.host=openReader({container:embedded?()=>document.getElementById('readerSlot'):undefined,paper:''' + json.dumps({'id': paper, 'title': 'Synthetic', 'pdf_url': '/original.pdf'}) + ''',principal:'reader_one',onReady:s=>signals.push({paperId:s.paperId,fingerprint:s.fingerprint}),onClose:()=>window.readerClosed=true});};window.ready=true;</script><div id="readerSlot" style="width:700px;height:600px"></div>''').encode()); return
            if parsed.path in ('/static/paper_fullscreen_reader.js', '/static/paper_reader.js', '/static/paper_personal.js', '/static/paper_detail.js'):
                self.send_response(200); self.send_header('Content-Type','text/javascript'); self.end_headers()
                self.wfile.write((root/'management_portal'/parsed.path.lstrip('/')).read_bytes()); return
            for d in definitions:
                if d.matches(self.command, parsed.path, 'dual-auth') or d.matches(self.command, parsed.path, 'protected'):
                    if d.phase == 'protected' and not http_auth._check_session(self): self.send_error(401); return
                    return d.handler(self, parsed, '')
            self.send_error(404)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        result = subprocess.run(['node',str(root/'tests/cases/javascript'/script),
            f'http://127.0.0.1:{server.server_port}',browser,http_auth._SESSION_COOKIE,*tokens],capture_output=True,text=True,timeout=90,env={**os.environ, 'TMPDIR': str(tmp_path)})
        assert result.returncode == 0, result.stdout + result.stderr
        if translation: assert len(google_calls) == 1, 'second operation must use the private cache'
        from management_common.directory_cleanup_store import get_directory_cleanup_store
        from management_portal import paper_reader_annotations
        from management_portal.paper_reader_service import production_service
        queue = get_directory_cleanup_store()
        before = {r['cleanup_id'] for r in queue.status()}
        original = paper_reader_annotations.purge_paper_records
        def fail(*args): raise RuntimeError('interrupted after unregister')
        monkeypatch.setattr(paper_reader_annotations, 'purge_paper_records', fail)
        with pytest.raises(RuntimeError, match='interrupted after unregister'):
            store.delete_paper(paper, actor='reader_one')
        monkeypatch.setattr(paper_reader_annotations, 'purge_paper_records', original)
        assert production_service().recover_pending_deletions() == [{'paper_id': paper, 'state': 'done'}]
        ids = {r['cleanup_id'] for r in queue.status()} - before
        assert ids
        for cleanup_id in ids: queue.run_pending(cleanup_id=cleanup_id)
        assert not bundle.exists()
    finally: server.shutdown(); server.server_close(); thread.join()

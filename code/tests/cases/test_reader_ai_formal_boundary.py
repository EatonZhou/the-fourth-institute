"""Actual Chat UI + prepare/adapter/native binding in disposable PG.

This intentionally reports the isolated environment's missing Paper application
binding instead of replacing the binding owner with a synthetic response.
"""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from management_portal import http_auth, web_accounts, resident_workspace_adapter
from management_portal.http_portal_session import PortalSessionHttp
from management_portal.portal_access_control import PortalAccessStore
from management_portal.paper_reader_service import production_service
from tests.cases.test_paper_reader_hooks import setup_paper
from tests.cases.test_portal_security_repair_wave1 import _configure_portal_session


def test_actual_chat_prepare_binding_boundary(tmp_path, monkeypatch):
    paper, _, _ = setup_paper(tmp_path, monkeypatch)
    from management_portal import personal_ama
    monkeypatch.setattr(personal_ama, 'provision_account', lambda *a, **k: None)
    access=PortalAccessStore(); access.upsert_user('reader_one',actor='CASE')
    _configure_portal_session(tmp_path,monkeypatch,access)
    token=web_accounts._issue_session_token('reader_one')
    document=production_service().open('reader_one',paper)['document']
    document={k:document[k] for k in ('paper_id','attachment_id','attachment_version')}
    seen=[]
    from management_portal.workbench_projections import native
    for operation in ('lookup','ensure'):
        original=getattr(native,operation)
        def observed(*args,_original=original,_operation=operation,**kwargs):
            try:return _original(*args,**kwargs)
            except Exception as exc:
                seen.append({'operation':_operation,'code':getattr(exc,'code',type(exc).__name__),'message':str(exc)})
                raise
        monkeypatch.setattr(native,operation,observed)
    def respond(req,status,value):
        req.send_response(status);req.send_header('Content-Type','application/json');req.end_headers();req.wfile.write(json.dumps(value).encode())
    def exception(req,exc):
        seen.append({'code':getattr(exc,'code',type(exc).__name__),'message':str(exc)})
        return respond(req,getattr(exc,'http_status',503),{'error':str(exc),'code':getattr(exc,'code',type(exc).__name__)})
    endpoint=PortalSessionHttp(respond=respond,exception_response=exception,
        read_json=lambda r:json.loads(r.rfile.read(int(r.headers['Content-Length']))),session_actor=http_auth._get_session_username,
        access=lambda:access,avatar=None,deployment=None,build_id='fixture',preferences=None,set_preferences=None,
        conversation_payload=None,workspace_adapter=resident_workspace_adapter)
    root=Path(__file__).resolve().parents[2]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def do_GET(self):
            if self.path=='/':
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<div id="chat"></div>');return
            self.send_error(404)
        def do_POST(self):
            assert self.path=='/chat-workspace/api/prepare'
            endpoint.chat_workspace_prepare(self,urlsplit(self.path),'')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        import os
        result=subprocess.run(['node',str(root/'tests/cases/javascript/reader_ai_formal_boundary.cjs'),
            f'http://127.0.0.1:{server.server_port}',http_auth._SESSION_COOKIE,token,json.dumps(document)],
            capture_output=True,text=True,timeout=35,env={**os.environ,'TFI_TEST_PYTHON':os.sys.executable})
        print(result.stdout);print('ACTUAL_OWNER_ERRORS',json.dumps(seen,ensure_ascii=False))
        assert result.returncode==0,result.stdout+result.stderr
        assert seen and all(e['code']=='projection.business_unbound' for e in seen),seen
        assert {e['operation'] for e in seen}=={'lookup','ensure'}
    finally:server.shutdown();server.server_close();thread.join()

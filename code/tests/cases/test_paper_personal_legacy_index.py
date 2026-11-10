"""Populated personal APIs must read both legacy TEXT and fresh JSONB indices."""
import json
import os
import subprocess
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import urllib.request
import urllib.error

import pytest
from management_portal import paper_recent, paper_search, presentation_store, web_accounts
from management_portal.portal_session_auth import PortalSessionAuthority
from management_portal.resource_index_store import IndexStore
from management_portal.resource_index_records import ChangeSet, resource_record
from tests.cases.test_resource_portal_routes import _QuietHandler


@pytest.mark.parametrize('storage', ['TEXT', 'JSONB'])
def test_populated_recent_and_favorites_cookie_routes(tmp_path, monkeypatch, storage):
    store = IndexStore()
    with store._connect() as db:
        db.execute(f'ALTER TABLE resource_index_records ALTER COLUMN record_json TYPE {storage} USING record_json::{storage.lower()}')
    ids = [f'paper_{i:012x}' for i in range(1, 5)]
    store.apply(ChangeSet(adapter='paper', upserts=tuple(resource_record(resource_id=i,
        resource_type='paper',adapter='paper',title='Synthetic '+i,attributes={
            'year':'2026','has_analysis':True,'has_pdf':False,'has_summary':False}) for i in ids)))
    monkeypatch.setattr(paper_search, 'get_resource_service', lambda: store)
    monkeypatch.setattr(paper_recent, 'is_deleted', lambda i: i == ids[2])
    monkeypatch.setattr('management_portal.resource_index_store.readable_flags',
        lambda rows,actor:[r['resource_id'] != ids[1] for r in rows])
    with presentation_store.database().transaction() as db:
        for actor, papers in [('alice',ids),('bob',[ids[0]])]:
            for i in papers:
                db.execute("INSERT INTO associations(actor,namespace,association_key,value_json,updated_at) VALUES (%s,%s,%s,'{}','2026-01-01T00:00:00Z')",(actor,paper_recent.NAMESPACE,i))
    presentation_store.set_favorite('alice','paper',ids[3],True)
    presentation_store.set_favorite('bob','paper',ids[0],True)
    key=tmp_path/'sessions';key.write_bytes(b'synthetic-session-key-for-tests-only')
    authority=PortalSessionAuthority(key,access_store=SimpleNamespace(is_active=lambda a:True,is_configured=lambda a:True,session_version=lambda a:0))
    monkeypatch.setattr(web_accounts,'_session_authority',lambda:authority)
    monkeypatch.setattr(web_accounts,'_portal_user_allowed',lambda a:a in ('alice','bob'))
    class Handler(_QuietHandler):
        def do_GET(self):
            if self.path == '/personal-fixture':
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers()
                self.wfile.write(b'''<!doctype html><meta charset="utf-8"><div class="leftBody"><div id="folderTree"></div></div>
<script src="/static/paper_personal.js"></script><script>
window.state={principalId:'alice',favoriteIds:new Set(),favoritePending:new Set()};
PortalPaperPersonal.mount({state,paperApi:{request:async url=>{const r=await fetch(url);const d=await r.json();if(!r.ok)throw Error(d.error?.message||d.error||'Portal request failed');return d;}},loadDetail:async()=>{},setMode:()=>{}});
</script>''');return
            return super().do_GET()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def browser(mode):
        script=Path(__file__).parent/'javascript/paper_personal_legacy.cjs'
        result=subprocess.run(['node',str(script),origin,authority.cookie_name,authority.issue('alice',max_age=600),mode],capture_output=True,text=True,timeout=30,env={**os.environ,'TMPDIR':str(tmp_path)})
        assert result.returncode==0,result.stdout+result.stderr
    def get(path,actor='alice'):
        headers={'Cookie':authority.cookie_name+'='+authority.issue(actor,max_age=600)} if actor else {}
        with urllib.request.urlopen(urllib.request.Request(origin+path,headers=headers)) as response:
            return json.load(response)
    try:
        first=get('/paper/api/recent-reading?limit=1&cursor=')
        assert [r['id'] for r in first['papers']] == [ids[3]]
        assert first['papers'][0]['favorite'] and first['next_cursor']
        second=get('/paper/api/recent-reading?limit=1&cursor='+urllib.parse.quote(first['next_cursor']))
        assert [r['id'] for r in second['papers']] == [ids[0]]
        assert second['next_cursor'] == ''
        assert [r['id'] for r in get('/paper/api/recent-reading?limit=50','bob')['papers']] == [ids[0]]
        path='/paper/api/library?compact=1&rows_only=1&favorite=1&limit=50&year=2026&analysis=ready'
        assert [r['id'] for r in get(path)['papers']] == [ids[3]]
        assert [r['id'] for r in get(path,'bob')['papers']] == [ids[0]]
        with pytest.raises(urllib.error.HTTPError) as error:get('/paper/api/recent-reading?limit=50',actor=None)
        assert error.value.code==401
        with pytest.raises(urllib.error.HTTPError) as error:get('/paper/api/recent-reading?actor=bob')
        assert error.value.code==400
        if storage == 'TEXT':
            browser('healthy')
            with store._connect() as db:
                db.execute('UPDATE resource_index_records SET record_json=%s WHERE resource_id=%s', ('{broken-json',ids[3]))
            with pytest.raises(urllib.error.HTTPError) as error:get('/paper/api/recent-reading?limit=50')
            assert error.value.code==500
            assert 'error' in json.loads(error.value.read())
            browser('broken')
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)

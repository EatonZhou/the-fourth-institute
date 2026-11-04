"""Fixed report quality and the shared review lifecycle; isolated resource ACLs.

:module: resource.service
:design: decision_e3ced6d30f6b
"""
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from management_portal import home_report_http as http
from management_portal import home_report_review as review
from management_portal.home_explanation_review import ReviewService
from management_portal.home_report_persistent_media import PersistentMedia
from management_portal.resources.errors import ResourceVersionConflict
from tests.cases.test_home_report_persistence import reports, RID, KEY, TARGET, renderer


@pytest.fixture
def auditing(reports, tmp_path, monkeypatch):
    store, state = reports
    calls = []
    evidence = b'Actual component inspection evidence in an isolated contract test'
    class Resources:
        def resolve(self, address, *, actor):
            if state.get('review_denied'):
                raise PermissionError('review source revoked')
            return {'address': address}
        def read_content(self, address, *, actor, max_bytes):
            self.resolve(address, actor=actor)
            if address == 'workspace_123456789abc/file/evidence.md':
                return evidence, 'text/plain', {}
            name = address.split('/file/')[1]
            number = int(address.split('/revision/')[1].split('/')[0])
            return store.file(RID, number, name, 'alice')[0].read_bytes(), '', {}
    def call(actor, instruction, args, key=''):
        calls.append((actor,instruction,args,key))
        if instruction == 'SearchResidents':
            return {'residents':[{'resident_id':'resident_123456789abd','title':'解释页独立检查','can_send':True}]}
        if instruction == 'DeliverMessage':
            if state.get('submit_failed'): raise PermissionError('not permitted')
            return {'message_id':'msg_review'}
        raise AssertionError(instruction)
    service = ReviewService(root=tmp_path/'reviews', resources=Resources(), call=call)
    monkeypatch.setattr(review,'ReviewService',lambda:service)
    return service, calls, [{'address':'workspace_123456789abc/file/evidence.md','sha256':hashlib.sha256(evidence).hexdigest()}]


def create(store, level):
    return store.materialize({'target':TARGET,'origin_key':KEY,'quality':level},'alice',lambda fields:store.create(RID,fields,'alice'))


def test_quality_origin_revision_and_markdown_are_fixed(reports, auditing):
    store, state = reports
    first = create(store,'low')
    assert first['format']=='markdown' and first['quality']=='low'
    assert store.file(RID,1,'report.md','alice')[0].read_text() == first['markdown']
    state['body'] = 'new source'
    assert create(store,'low')['version'] == first['version']
    with pytest.raises(ResourceVersionConflict): create(store,'high')
    store.refresh(RID,'alice',1,quality_level='medium')
    second = store.response(RID,'alice',2)
    assert second['quality']=='medium' and second['format']=='interactive' and second['version']!=first['version']
    assert store.origin('alice',KEY)['quality']=='low'
    assert store.response(RID,'alice',1)['markdown']==first['markdown']
    with pytest.raises(ValueError): store.refresh(RID,'alice',2,quality_level='ultra')


def test_high_reject_modify_review_reopen_no_dispatch_and_old_pass_invalid(reports,auditing):
    store,_ = reports
    service,calls,evidence = auditing
    first=create(store,'high')
    assert first['review']['status']=='not_requested' and not calls
    sent=review.submit(store,RID,1,'alice')
    assert sent['status']=='submitted'
    with pytest.raises(PermissionError):
        service.verdict(sent['id'],'alice',digest=first['version'],status='passed',evidence=evidence)
    rejected=service.verdict(sent['id'],'resident_123456789abd',digest=first['version'],status='changes_requested',evidence=evidence,feedback='说明因果，并明确限制')
    assert rejected['status']=='changes_requested'
    store.refresh(RID,'alice',1,content={'markdown':'# 修订\n\n增加因果、实证与限制。'})
    second=store.response(RID,'alice',2)
    assert second['version']!=first['version'] and second['review']['status']=='not_requested'
    sent2=review.submit(store,RID,2,'alice')
    assert sent2['id']!=sent['id']
    service.verdict(sent2['id'],'resident_123456789abd',digest=second['version'],status='passed',evidence=evidence)
    count=len(calls)
    assert store.response(RID,'alice',2)['review']['status']=='passed'
    assert store.origin('alice',KEY)['review']['status']=='changes_requested'
    assert len(calls)==count
    store.refresh(RID,'alice',2,content={'markdown':'# 修订三\n\n内容再次改变。'})
    assert store.response(RID,'alice',3)['review']['status']=='not_requested'
    assert store.response(RID,'alice',2)['review']['status']=='passed'
    with pytest.raises(ResourceVersionConflict): store.refresh(RID,'alice',2,content={'markdown':'late'})


def test_cancel_failed_submission_and_permissions_are_not_passes(reports,auditing):
    store,state=reports
    service,calls,_=auditing
    create(store,'high')
    state['submit_failed']=True
    failed=review.submit(store,RID,1,'alice')
    assert failed['status']=='submission_failed'
    assert store.response(RID,'alice',1)['review']['status']=='submission_failed'
    state['submit_failed']=False
    sent=review.submit(store,RID,1,'alice')
    service.cancel(sent['id'],'alice')
    count=len(calls)
    assert review.submit(store,RID,1,'alice')['status']=='cancelled'
    assert len(calls)==count
    state['deny']=True
    with pytest.raises(PermissionError): store.response(RID,'alice',1)


def test_rejected_interactive_report_changes_actual_graph_frames_and_voice(reports,auditing,monkeypatch):
    from PIL import ImageFont
    from management_portal import home_report_store
    from management_portal.home_report import build_report
    from management_portal.home_report_reasoning import build_reasoning
    from management_portal.home_report_media import chapters
    from management_portal.home_report_visual import frame
    from tests.cases.test_home_report_reasoning import report as graph
    store,_=reports
    service,_,evidence=auditing
    original=build_report(TARGET,read=store.read)
    original.update({key:value for key,value in graph().items() if key!='target'})
    original['reasoning']=build_reasoning(original)
    monkeypatch.setattr(home_report_store,'build_report',lambda *a,**kw:original)
    first=create(store,'high')
    sent=review.submit(store,RID,1,'alice')
    service.verdict(sent['id'],'resident_123456789abd',digest=first['version'],status='changes_requested',evidence=evidence,feedback='起点标识和图位置不清，开场讲稿需改')
    steps=[{} for _ in first['reasoning']['steps']]
    steps[0]={'title':'核对真实起点','speech':'先固定这个起点，再逐步追踪依赖，不能将图路径当作业务完成。'}
    store.refresh(RID,'alice',1,content={'presentation':{'question':'当前图能证明什么？','nodes':[{'id':'a','title':'明确起点','x':.28,'y':.25}],'steps':steps}})
    second=store.response(RID,'alice',2)
    assert second['quality']=='high' and second['format']=='interactive'
    assert second['review']['status']=='not_requested' and second['media'] is None
    assert second['reasoning']['nodes']!=first['reasoning']['nodes']
    assert second['reasoning']['edges']==first['reasoning']['edges']
    assert chapters(second)[0]['speech']!=chapters(first)[0]['speech']
    fonts={size:ImageFont.load_default(size=size) for size in (16,18,20,22,24,32)}
    assert frame(second['reasoning'],0,2,4,'',fonts).tobytes()!=frame(first['reasoning'],0,2,4,'',fonts).tobytes()
    assert store.file(RID,2,'report.json','alice')[0].read_bytes()!=store.file(RID,1,'report.json','alice')[0].read_bytes()
    sent2=review.submit(store,RID,2,'alice')
    assert sent2['id']!=sent['id'] and sent2['subject']['digest']==second['version']
    with pytest.raises(ValueError): store.refresh(RID,'alice',2,content={'presentation':{'nodes':[{'id':'invented','x':.5}]}})


def test_media_quality_has_separate_fixed_bytes_and_never_reuses_other_ready(reports,auditing):
    store,_=reports
    create(store,'medium')
    levels=[]
    def render(snapshot,path):
        levels.append(snapshot['media_quality'])
        value=renderer({**snapshot,'version':snapshot['version']+snapshot['media_quality']},path)
        return value
    media=PersistentMedia(store,render)
    for level in ['low','medium','high']:
        media.start(RID,1,'alice',quality=level)
        for _ in range(300):
            result=media.status(RID,1,'alice',quality=level)
            if result and result['status']!='pending' and not media.active(RID,1,level): break
            time.sleep(.01)
        assert result['status']=='ready', result
        if level=='high': assert result['review']['status']!='passed'
    assert levels==['low','medium','high']
    assert len({media.file(RID,1,'video.mp4','alice',quality=q)[0].read_bytes() for q in levels})==3
    assert media.start(RID,1,'alice',quality='low')['status']=='ready'
    assert len(levels)==3


def test_cancelled_media_cannot_be_published_by_a_late_renderer(reports,auditing):
    store,_=reports
    create(store,'medium')
    started=threading.Event(); release=threading.Event()
    def render(snapshot,directory):
        started.set();release.wait(3)
        return renderer(snapshot,directory)
    media=PersistentMedia(store,render)
    media.start(RID,1,'alice',quality='low')
    assert started.wait(1)
    assert media.cancel(RID,1,'alice',quality='low')['status']=='cancelled'
    release.set()
    for _ in range(300):
        if not media.active(RID,1,'low'): break
        time.sleep(.01)
    assert media.status(RID,1,'alice',quality='low')['status']=='cancelled'
    assert media.start(RID,1,'alice',quality='low',retry=True)['status']=='cancelled'
    with pytest.raises(FileNotFoundError):media.file(RID,1,'video.mp4','alice',quality='low')


def test_real_http_quality_save_read_and_refresh(reports,auditing,monkeypatch):
    """Real socket + production handlers, isolated auth/resource adapter, no User impersonation."""
    store,_=reports
    from management_portal import resources
    from management_portal.resources.providers import report as provider
    monkeypatch.setattr(http,'ReportStore',lambda:store)
    monkeypatch.setattr(provider,'ReportStore',lambda:store)
    class Service:
        def register(self,kind,fields,*,actor,operation_id):
            return store.create(RID,fields,actor)
        def register_mutation_provider(self,kind,**methods):
            self.methods=methods
    registry=Service();provider.install_mutation_provider(registry)
    monkeypatch.setattr(resources,'get_resource_service',lambda:registry)
    monkeypatch.setattr(http,'isa_reader',lambda actor:lambda name,args:registry.methods['update'](args['resource_id'],args['changes'],actor=actor))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            http._get(self,urlparse(self.path),'alice')
        def do_POST(self):
            {'/api/home-report/save':http._save,'/api/home-report/refresh':http._refresh}[urlparse(self.path).path](self,urlparse(self.path),'alice')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def send(path,payload=None):
        req=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(payload).encode() if payload else None,headers={'Content-Type':'application/json'})
        with urlopen(req) as response: return response.status,json.load(response)
    try:
        code,first=send('/api/home-report/save',{'target':TARGET,'origin_key':KEY,'quality':'low'})
        assert code==200 and first['quality']=='low'
        _,fixed=send(f'/api/home-report?kind=report&id={RID}&revision=1&quality=high')
        assert fixed['quality']=='low' and fixed['version']==first['version']
        with pytest.raises(HTTPError) as conflict:
            send('/api/home-report/save',{'target':TARGET,'origin_key':KEY,'quality':'medium'})
        assert conflict.value.code==409
        _,new=send('/api/home-report/refresh',{'report_id':RID,'expected_revision':1,'quality':'high','content':{'markdown':'# 审核稿\n\n固定新版'}})
        assert new['revision']==2 and new['quality']=='high' and new['review']['status']=='submitted'
        assert store.origin('alice',KEY)['revision']==1
    finally:
        server.shutdown();server.server_close();thread.join()

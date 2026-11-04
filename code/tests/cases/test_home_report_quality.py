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
from management_portal.home_explanation_review import ReviewService, AuthoringService
from management_portal.home_report_persistent_media import PersistentMedia
from management_portal.resources.errors import ResourceVersionConflict
from tests.cases.test_home_report_persistence import reports, RID, TARGET, renderer

RESPONSIBLE='resident_123456789abe'
ORIGIN={'resident_id':RESPONSIBLE,'message_id':'msg_123456789abe','fence_index':0}
INTENT={'action':'open','view':'report','target':TARGET}
KEY='home-message/v1:'+hashlib.sha256(json.dumps([RESPONSIBLE,ORIGIN['message_id'],0,INTENT],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
MESSAGE='```portal-home-display\n'+json.dumps({'protocol':'portal-home-display/v1','request_id':'formal-request','version':1,**INTENT})+'\n```'


@pytest.fixture
def auditing(reports, tmp_path, monkeypatch):
    store, state = reports
    original_read=store.read
    def read(name,args):
        if name=='GetMessage' and args=={'resident_id':RESPONSIBLE,'message_id':ORIGIN['message_id']}:
            if state.get('origin_denied'): raise PermissionError('author message revoked')
            return {'message':{'message_id':ORIGIN['message_id'],'role':state.get('origin_role','assistant'),
                               'content_parts':[{'type':'text','text':state.get('origin_text',MESSAGE)}]}}
        return original_read(name,args)
    store.read=read
    original_authorize=store.authorize
    store.authorize=lambda actor,operation,identifier: original_authorize('alice' if actor==RESPONSIBLE else actor,operation,identifier)
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
    authoring = AuthoringService(root=tmp_path/'authoring', resources=Resources(), call=call)
    monkeypatch.setattr(review,'ReviewService',lambda:service)
    monkeypatch.setattr(review,'AuthoringService',lambda:authoring)
    return service, calls, [{'address':'workspace_123456789abc/file/evidence.md','sha256':hashlib.sha256(evidence).hexdigest()}]


def create(store, level):
    return store.materialize({'target':TARGET,'origin_key':KEY,'origin_context':ORIGIN,'quality':level},'alice',lambda fields:store.create(RID,fields,'alice'))


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
    store.refresh(RID,RESPONSIBLE,1,content={'markdown':'# 修订\n\n增加因果、实证与限制。'})
    second=store.response(RID,'alice',2)
    assert second['author']=='alice' and second['modified_by']==RESPONSIBLE
    assert second['version']!=first['version'] and second['review']['status']=='not_requested'
    sent2=review.submit(store,RID,2,'alice')
    assert sent2['id']!=sent['id']
    assert sent2['round']==2 and sent2['subject']['previous_review_id']==sent['id']
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


def test_rejected_interactive_report_changes_actual_graph_frames_and_captions(reports,auditing,monkeypatch):
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
    import os
    if output:=os.environ.get('REPORT_QUALITY_RENDER_ARTIFACTS'):
        from pathlib import Path
        from management_portal.media_tools import capabilities
        directory=Path(output);directory.mkdir(parents=True,exist_ok=True)
        fonts={size:ImageFont.truetype(capabilities()['font'],size) for size in fonts}
        before=frame(first['reasoning'],0,2,4,'',fonts);after=frame(second['reasoning'],0,2,4,'',fonts)
        before.save(directory/'before.png');after.save(directory/'after.png')
        (directory/'result.json').write_text(json.dumps({'scope':'isolated sources; production fixed store, correction, chapters and frame renderer; no audio/MP4 generation',
            'old_version':first['version'],'new_version':second['version'],'quality':second['quality'],
            'old_caption':chapters(first)[0]['speech'],'new_caption':chapters(second)[0]['speech'],
            'old_frame_sha256':hashlib.sha256(before.tobytes()).hexdigest(),'new_frame_sha256':hashlib.sha256(after.tobytes()).hexdigest(),
            'old_review':'changes_requested','new_review':second['review']['status']},ensure_ascii=False,indent=2))
    assert store.file(RID,2,'report.json','alice')[0].read_bytes()!=store.file(RID,1,'report.json','alice')[0].read_bytes()
    sent2=review.submit(store,RID,2,'alice')
    assert sent2['id']!=sent['id'] and sent2['subject']['digest']==second['version']
    assert sent2['round']==2 and sent2['subject']['previous_review_id']==sent['id']
    with pytest.raises(ValueError): store.refresh(RID,'alice',2,content={'presentation':{'nodes':[{'id':'invented','x':.5}]}})


def test_report_corrections_continue_past_three_rounds_in_shared_authority(reports,auditing):
    store,_=reports
    service,calls,evidence=auditing
    create(store,'high')
    previous=None
    for number in range(1,4):
        sent=review.submit(store,RID,number,'alice')
        assert sent['round']==number
        if previous:
            assert sent['subject']['previous_review_id']==previous
        service.verdict(sent['id'],'resident_123456789abd',digest=sent['subject']['digest'],
                        status='changes_requested',evidence=evidence,feedback='实际产物尚未达标')
        store.refresh(RID,'alice',number,content={'markdown':f'第{number+1}版实际修订'})
        previous=sent['id']
    fourth=review.submit(store,RID,4,'alice')
    assert fourth['round']==4 and fourth['status']=='submitted'
    assert fourth['subject']['previous_review_id']==previous
    service.verdict(fourth['id'],'resident_123456789abd',digest=fourth['subject']['digest'],
                    status='passed',evidence=evidence,feedback='修订后的实际产物达标')
    count=len(calls)
    assert store.response(RID,'alice',4)['review']['status']=='passed'
    assert len(calls)==count


def test_active_legacy_media_is_not_replaced_by_an_authoring_request(reports,auditing,monkeypatch):
    store,_=reports
    create(store,'medium')
    media=PersistentMedia(store,lambda *args:pytest.fail('active render must not restart'))
    snapshot=store.get(RID,'alice',1)[2]
    (store.directory(RID)/'1/media-state.json').write_text(json.dumps({
        'status':'pending','version':snapshot['version'],'quality':'medium'}))
    monkeypatch.setattr(media,'active',lambda *args:True)
    before=len(auditing[1])
    assert media.start(RID,1,'alice',quality='medium')['status']=='pending'
    assert len(auditing[1])==before


def test_media_quality_has_separate_fixed_bytes_and_never_reuses_other_ready(reports,auditing,monkeypatch):
    store,_=reports
    from management_portal import home_report_store
    from management_portal.home_report import build_report
    from management_portal.home_report_reasoning import build_reasoning
    from tests.cases.test_home_report_reasoning import report as graph
    original=build_report(TARGET,read=store.read)
    original.update({key:value for key,value in graph().items() if key!='target'})
    original['reasoning']=build_reasoning(original)
    monkeypatch.setattr(home_report_store,'build_report',lambda *a,**kw:original)
    create(store,'medium')
    levels=[]
    def render(snapshot,path):
        levels.append(snapshot['media_quality'])
        value=renderer({**snapshot,'version':snapshot['version']+snapshot['media_quality']},path)
        return value
    media=PersistentMedia(store,render)
    number=1
    fixed={}
    for level in ['low','medium','high']:
        if level!='low':
            task=media.start(RID,number,'alice',quality=level)
            assert task['status']=='authoring' and task['authoring']['status']=='submitted'
            assert level not in levels
            assert media.start(RID,number,'alice',quality=level)['authoring']['id']==task['authoring']['id']
            store.refresh(RID,RESPONSIBLE,number,content={'authoring_id':task['authoring']['id'],
                'presentation':{'nodes':[{'id':'a','title':f'{level}实际改稿','x':number/10}]}})
            assert media.status(RID,number,'alice',quality=level)['status']=='authored'
            number+=1
        fixed[level]=number
        media.start(RID,number,'alice',quality=level)
        for _ in range(300):
            result=media.status(RID,number,'alice',quality=level)
            if result and result['status']!='pending' and not media.active(RID,number,level): break
            time.sleep(.01)
        assert result['status']=='ready', result
        if level=='high': assert result['review']['status']!='passed'
    assert levels==['low','medium','high']
    assert len({media.file(RID,fixed[q],'video.mp4','alice',quality=q)[0].read_bytes() for q in levels})==3
    assert media.start(RID,1,'alice',quality='low')['status']=='ready'
    assert len(levels)==3
    requests=[json.loads(args['message']['content_parts'][0]['text']) for _,instruction,args,_ in auditing[1]
              if instruction=='DeliverMessage' and 'portal-quality-authoring/v1' in args['message']['content_parts'][0]['text']]
    assert requests[0]['policy']['quality']=='medium' and '3Blue1Brown' in requests[0]['policy']['guidance']
    assert requests[0]['subject']['responsible_resident_id']==RESPONSIBLE
    assert 'UpdateResource' in requests[0]['subject']['task']


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


def test_formal_author_source_rejects_user_code_wrong_key_and_changed_message(reports,auditing):
    from management_portal.home_report_origin import verified_source
    store,state=reports
    binding=verified_source('alice',ORIGIN,KEY,TARGET,read=store.read)
    assert binding['resident_id']==RESPONSIBLE and binding['kind']=='assistant_message'
    for focus in ('','节点'):
        intent={**INTENT,'target':{**TARGET,'focus':focus}}
        expected='home-message/v1:'+hashlib.sha256(json.dumps([RESPONSIBLE,ORIGIN['message_id'],0,intent],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        state['origin_text']='```portal-home-display\n'+json.dumps({'protocol':'portal-home-display/v1','request_id':'r','version':1,**intent,'target':{**intent['target'],'id':' '+TARGET['id']+' '}})+'\n```'
        assert verified_source('alice',ORIGIN,expected,intent['target'],read=store.read)['resident_id']==RESPONSIBLE
    state['origin_text']=MESSAGE
    state['origin_role']='user'
    with pytest.raises(ValueError): verified_source('alice',ORIGIN,KEY,TARGET,read=store.read)
    state['origin_role']='assistant'
    with pytest.raises(ValueError): verified_source('alice',ORIGIN,'home-message/v1:'+'0'*64,TARGET,read=store.read)
    state['origin_text']=MESSAGE[:-3]
    with pytest.raises(ValueError): verified_source('alice',ORIGIN,KEY,TARGET,read=store.read)
    state['origin_text']=MESSAGE
    create(store,'high')
    state['origin_text']='原文已变更\n'+MESSAGE
    with pytest.raises(ValueError,match='已改变'): review.submit(store,RID,1,'alice')
    state['origin_denied']=True
    with pytest.raises(PermissionError): verified_source('alice',ORIGIN,KEY,TARGET,read=store.read)


@pytest.mark.parametrize('focus',['','节点'*150,'😀'*150,'a'*199+'😀'])
def test_origin_after_python_fence_matches_host_ordinal_and_utf16_focus(reports,auditing,focus):
    from pathlib import Path
    import subprocess
    from management_portal.home_report_origin import verified_source
    store,state=reports
    host=Path(__file__).resolve().parents[2]/'management_portal/static/home_dialogue_state.js'
    raw={**INTENT,'target':{**TARGET,'focus':focus},'quality':'high'}
    for index,extra in [(0,''),(1,'```portal-home-display\n{}\n```\n')]:
        context={**ORIGIN,'fence_index':index}
        # Actual Host normalizeIntent, with JSON.stringify/TextEncoder-equivalent
        # Node SHA, avoids duplicating Python's normalization in the assertion.
        javascript="""const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
global.window={};vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const v=JSON.parse(fs.readFileSync(0,'utf8'));
const {quality,...intent}=window.PortalHomeDialogueState.normalizeIntent(v.raw);
const key='home-message/v1:'+crypto.createHash('sha256').update(JSON.stringify([v.resident,v.message,v.index,intent]),'utf8').digest('hex');
process.stdout.write(JSON.stringify({intent,key}));"""
        expected=json.loads(subprocess.check_output(['node','-e',javascript,str(host)],input=json.dumps({
            'raw':raw,'resident':RESPONSIBLE,'message':ORIGIN['message_id'],'index':index}).encode()))
        state['origin_text']='```python\nprint("ordinary code")\n```\n'+extra+'```portal-home-display\n'+json.dumps({
            'protocol':'portal-home-display/v1','request_id':'r','version':1,**raw})+'\n```'
        result=verified_source('alice',context,expected['key'],expected['intent']['target'],read=store.read)
        assert result['source']['fence_index']==index
        assert result['intent']==expected['intent']
        wrong={**context,'fence_index':index+1}
        with pytest.raises(ValueError,match='不存在'):verified_source('alice',wrong,expected['key'],expected['intent']['target'],read=store.read)


def test_unassigned_user_report_cannot_pretend_resident_editing_loop(reports,auditing):
    store,_=reports
    _,calls,_=auditing
    store.create(RID,{'target':TARGET,'quality':'high'},'alice')
    assert review.submit(store,RID,1,'alice')['status']=='awaiting_author'
    media=PersistentMedia(store,lambda *args:pytest.fail('no unassigned renderer'))
    state=media.start(RID,1,'alice',quality='medium')
    assert state['status']=='authoring' and state['authoring']['status']=='awaiting_author'
    count=len(calls)
    assert media.status(RID,1,'alice',quality='medium')['authoring']['status']=='awaiting_author'
    assert len(calls)==count==0
    assert media.cancel(RID,1,'alice',quality='medium')['status']=='cancelled'
    assert media.start(RID,1,'alice',quality='medium')['status']=='cancelled'


def test_authoring_requires_actual_responsible_edit_and_honors_cancel(reports,auditing,monkeypatch):
    from management_portal import home_report_store
    from management_portal.home_report import build_report
    from management_portal.home_report_reasoning import build_reasoning
    from tests.cases.test_home_report_reasoning import report as graph
    store,_=reports
    original=build_report(TARGET,read=store.read)
    original.update({key:value for key,value in graph().items() if key!='target'})
    original['reasoning']=build_reasoning(original)
    monkeypatch.setattr(home_report_store,'build_report',lambda *a,**kw:original)
    create(store,'medium')
    media=PersistentMedia(store,lambda *args:pytest.fail('not authored yet'))
    task=media.start(RID,1,'alice',quality='medium')['authoring']
    change={'authoring_id':task['id'],'presentation':{'nodes':[{'id':'a','x':.25}]}}
    with pytest.raises(PermissionError):store.refresh(RID,'alice',1,content=change)
    with pytest.raises(ValueError,match='实际修改'):store.refresh(RID,RESPONSIBLE,1,content={'authoring_id':task['id'],'presentation':{}})
    assert store.record(RID)['revision']==1
    media.cancel(RID,1,'alice',quality='medium')
    with pytest.raises(ValueError,match='已取消'):store.refresh(RID,RESPONSIBLE,1,content=change)
    assert store.record(RID)['revision']==1


def test_real_http_quality_save_read_and_refresh(reports,auditing,monkeypatch):
    """Real socket + production handlers, isolated auth/resource adapter, no User impersonation."""
    store,_=reports
    from management_portal import resources
    from management_portal import home_report_store
    from management_portal.home_report import build_report
    from management_portal.home_report_reasoning import build_reasoning
    from management_portal.resources.providers import report as provider
    from tests.cases.test_home_report_reasoning import report as graph
    original=build_report(TARGET,read=store.read)
    original.update({key:value for key,value in graph().items() if key!='target'})
    original['reasoning']=build_reasoning(original)
    monkeypatch.setattr(home_report_store,'build_report',lambda *a,**kw:original)
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
        code,first=send('/api/home-report/save',{'target':TARGET,'origin_key':KEY,'origin_context':ORIGIN,'quality':'low'})
        assert code==200 and first['quality']=='low'
        _,fixed=send(f'/api/home-report?kind=report&id={RID}&revision=1&quality=high')
        assert fixed['quality']=='low' and fixed['version']==first['version']
        with pytest.raises(HTTPError) as conflict:
            send('/api/home-report/save',{'target':TARGET,'origin_key':KEY,'quality':'medium'})
        assert conflict.value.code==409
        _,new=send('/api/home-report/refresh',{'report_id':RID,'expected_revision':1,'quality':'high','content':{'markdown':'# 审核稿\n\n固定新版','presentation':{'nodes':[{'id':'a','title':'经接口修改的起点','x':.3}]}}})
        assert new['revision']==2 and new['quality']=='high' and new['review']['status']=='submitted'
        node=next(item for item in new['reasoning']['nodes'] if item['id']=='a')
        assert node['x']==.3 and node['title']=='经接口修改的起点'
        assert new['review']['subject']['digest']==new['version']!=first['version']
        _,old=send(f'/api/home-report?kind=report&id={RID}&revision=1')
        assert next(item for item in old['reasoning']['nodes'] if item['id']=='a')['title']!='经接口修改的起点'
        assert store.origin('alice',KEY)['revision']==1
    finally:
        server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('may_update',[False,True],ids=['responsible-without-write','responsible-with-existing-write'])
def test_responsible_resident_http_update_requires_existing_resource_permission(reports,auditing,monkeypatch,may_update):
    """Production HTTP/UpdateResource provider; explicit isolated principals and ACL, no live tokens."""
    store,_=reports
    from management_portal import home_report_store
    from management_portal.home_report import build_report
    from management_portal.home_report_reasoning import build_reasoning
    from management_portal.resources.providers import report as provider
    from tests.cases.test_home_report_reasoning import report as graph
    original=build_report(TARGET,read=store.read)
    original.update({key:value for key,value in graph().items() if key!='target'})
    original['reasoning']=build_reasoning(original)
    monkeypatch.setattr(home_report_store,'build_report',lambda *a,**kw:original)
    create(store,'medium')
    task=review.submit_authoring(store,RID,1,'alice','medium')['authoring']
    assert task['status']=='submitted' and task['subject']['responsible_resident_id']==RESPONSIBLE
    assert store.record(RID)['created_by']=='alice'
    original_authorize=store.authorize
    checked=[]
    def authorize(actor,operation,identifier):
        checked.append((actor,operation,identifier))
        if actor==RESPONSIBLE and operation=='UpdateResource' and not may_update:
            raise PermissionError(f'UpdateResource {identifier}: {actor} has no write permission')
        return original_authorize(actor,operation,identifier)
    store.authorize=authorize
    monkeypatch.setattr(http,'ReportStore',lambda:store)
    monkeypatch.setattr(provider,'ReportStore',lambda:store)
    class Registry:
        def register_mutation_provider(self,kind,**methods): self.methods=methods
    registry=Registry();provider.install_mutation_provider(registry)
    updates=[]
    def isa(actor):
        def call(name,args):
            assert name=='UpdateResource' and actor==RESPONSIBLE
            updates.append((actor,name,args['resource_id']))
            return registry.methods['update'](args['resource_id'],args['changes'],actor=actor)
        return call
    monkeypatch.setattr(http,'isa_reader',isa)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            {'/api/home-report/refresh':http._refresh,'/api/home-report/media':http._generate}[urlparse(self.path).path](self,urlparse(self.path),RESPONSIBLE)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def post(path,body):
        request=Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:
            with urlopen(request) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    before=store.record(RID)
    deliveries=len(auditing[1])
    try:
        code,result=post('/api/home-report/refresh',{'report_id':RID,'expected_revision':1,'content':{
            'authoring_id':task['id'],'presentation':{'nodes':[{'id':'a','title':'负责者实际修改','x':.2}]}}})
        assert updates==[(RESPONSIBLE,'UpdateResource',RID)]
        assert (RESPONSIBLE,'UpdateResource',RID) in checked
        if may_update:
            assert code==200 and result['revision']==2
            assert result['author']=='alice' and result['modified_by']==RESPONSIBLE
            assert result['video_authoring']['resident_id']==RESPONSIBLE
            assert next(n for n in result['reasoning']['nodes'] if n['id']=='a')['title']=='负责者实际修改'
        else:
            assert code==403 and 'UpdateResource' in str(result) and RID in str(result)
            code,denied=post('/api/home-report/media',{'report_id':RID,'revision':1,'quality':'medium'})
            assert code==403 and RID in str(denied)
            assert store.record(RID)==before
            assert len(auditing[1])==deliveries
            assert not (store.directory(RID)/'2').exists()
            assert not list((store.directory(RID)/'1').glob('media*'))
    finally:
        server.shutdown();server.server_close();thread.join()

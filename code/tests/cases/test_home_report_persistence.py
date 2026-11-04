"""Persistent presentations: origins, immutable versions, assets and cleanup.

:module: resource.service
:design: decision_b12782e7f490
"""
import hashlib
import json
import threading
import time
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from management_portal.home_report_store import ReportStore, COLLECTION, ORIGINS
from management_portal.home_report_media import FILES
from management_portal.home_report_persistent_media import PersistentMedia
from management_portal.resources.errors import ResourceVersionConflict
from tests.cases.test_workspace_resource_rename import workspace

RID = 'report_123456789abc'
TARGET = {'kind':'task','id':'resident_123456789abc/msg_123456789abc'}
KEY = 'home-request/v1:12345678-1234-1234-1234-123456789abc'


@pytest.fixture
def reports(tmp_path):
    state = {'body':'核对输入与产物','deny':False,'deny_report':False,'reads':0}
    def read(name,args):
        state['reads'] += 1
        if state['deny']:
            raise PermissionError('source revoked')
        if name == 'GetMessage':
            return {'message':{'content_parts':[{'type':'text','text':state['body']}]}}
        if name == 'GetInputState':
            return {'input_state':{'state':'pending'}}
        raise AssertionError(name)
    def authorize(actor, operation, identifier):
        if state['deny_report'] or actor != 'alice':
            raise PermissionError('report denied')
    return ReportStore(root=tmp_path/'reports',authorize=authorize,read=read),state


def create(store, key=KEY, identifier=RID):
    return store.materialize({'target':TARGET,'origin_key':key},'alice',
                             lambda fields:store.create(identifier,fields,'alice'))


def test_origin_returns_first_snapshot_when_sources_change_and_is_actor_scoped(reports):
    store,state = reports
    first = create(store)
    state['body'] = '新的业务事实'
    second = store.materialize({'target':TARGET,'origin_key':KEY,'version':'outdated'},'alice',
                               lambda fields:pytest.fail('origin must not create again'))
    assert first['version'] == second['version']
    assert second['revision'] == 1
    assert second['goal'] == first['goal']
    with pytest.raises(FileNotFoundError):
        store.origin('bob',KEY)
    independent = create(store,KEY[:-1]+'d','report_123456789abd')
    assert independent['report_id'] != first['report_id']
    state['deny'] = True
    with pytest.raises(PermissionError):
        store.origin('alice',KEY)


def test_refresh_keeps_original_binding_assets_and_rejects_stale_cas(reports):
    store,state = reports
    first = create(store)
    before = store.file(RID,1,'report.html','alice')[0].read_bytes()
    state['body'] = '新输入与明确产物'
    store.refresh(RID,'alice',1)
    assert store.response(RID,'alice',2)['version'] != first['version']
    assert store.origin('alice',KEY)['revision'] == 1
    assert store.file(RID,1,'report.html','alice')[0].read_bytes() == before
    with pytest.raises(ResourceVersionConflict):
        store.refresh(RID,'alice',1)
    with pytest.raises(ValueError):
        store.get('../escape','alice')
    with pytest.raises(FileNotFoundError):
        store.file(RID,1,'../report.json','alice')


def test_report_and_origin_commit_together_on_interrupted_creation(reports, monkeypatch):
    store,state = reports
    from management_portal.resources.registry_store import RegistryWriter
    original = RegistryWriter.put
    def fail(writer,collection,identifier,value):
        if collection == ORIGINS:
            raise RuntimeError('simulated before origin commit')
        return original(writer,collection,identifier,value)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryWriter,'put',fail)
        with pytest.raises(RuntimeError):
            create(store)
    assert store.registry.get(COLLECTION,RID) is None
    assert store.registry.get(ORIGINS,store.origin_id('alice',KEY)) is None
    original_snapshot=json.loads((store.directory(RID)/'1/report.json').read_text())
    state['body']='中断后来源已经改变，仍须恢复首次快照'
    restored = create(store)
    assert restored['report_id'] == RID
    assert restored['version']==original_snapshot['version']
    assert restored['checked_at']==original_snapshot['checked_at']
    assert list(store.record(RID)['revisions']) == ['1']


def renderer(report,directory):
    for filename in FILES:
        (directory / filename).write_bytes((filename+report['version']).encode())
    return {'duration':1,'chapters':[],'transcript':'测试音轨接口','generation_seconds':0.01}


def wait_ready(media):
    for _ in range(300):
        result = media.status(RID,1,'alice')
        if result and result['status'] != 'pending':
            assert result['status'] == 'ready',result
            return result
        time.sleep(.01)
    pytest.fail('media did not complete')


def test_media_survives_store_recreation_and_requires_current_permissions(reports):
    store,state=reports
    create(store)
    media=PersistentMedia(store,renderer)
    media.start(RID,1,'alice')
    result=wait_ready(media)
    first=media.file(RID,1,'video.mp4','alice')[0].read_bytes()
    recreated=ReportStore(root=store.root,authorize=store.authorize,read=store.read)
    restored=recreated.response(RID,'alice',1)
    assert restored['media']['assets']['video.mp4'] == hashlib.sha256(first).hexdigest()
    assert recreated.file(RID,1,'video.mp4','alice')[0].read_bytes()==first
    assert PersistentMedia(recreated,lambda *args:pytest.fail('must not regenerate')).start(RID,1,'alice')['status']=='ready'
    state['deny_report']=True
    with pytest.raises(PermissionError): recreated.response(RID,'alice',1)
    state['deny_report']=False
    state['deny']=True
    with pytest.raises(PermissionError): recreated.file(RID,1,'video.mp4','alice')


def test_exports_are_real_ooxml_and_escaped_html(reports):
    store,state=reports
    state['body']='<script>alert(1)</script>'
    create(store)
    html=store.file(RID,1,'report.html','alice')[0].read_text()
    assert '<script>' not in html and '&lt;script&gt;' in html
    ppt=store.file(RID,1,'slides.pptx','alice')[0]
    with ZipFile(ppt) as archive:
        assert 'ppt/presentation.xml' in archive.namelist()
        for name in archive.namelist():
            ElementTree.fromstring(archive.read(name))


def test_delete_unregistration_is_immediate_and_late_media_cannot_resurrect(reports):
    store,state=reports
    create(store)
    entered,release=threading.Event(),threading.Event()
    def slow(report,directory):
        entered.set()
        assert release.wait(5)
        return renderer(report,directory)
    media=PersistentMedia(store,slow)
    media.start(RID,1,'alice')
    assert entered.wait(3)
    try:
        result=store.delete(RID,'alice',1)
        assert result['registration_status']=='unregistered'
        assert result['directory_cleanup']['cleanup_ids']
        with pytest.raises(FileNotFoundError): store.get(RID,'alice')
    finally:
        release.set()
    from management_portal import business_tasks
    for _ in range(300):
        if not business_tasks.is_active(media.task(RID,1)): break
        time.sleep(.01)
    assert not (store.directory(RID)/'1/media').exists()
    from management_portal.resources.errors import ResourceOperationError
    with pytest.raises(ResourceOperationError,match='已删除'): store.origin('alice',KEY)


def test_formal_resource_registration_reopen_and_directory_cleanup(workspace,reports,monkeypatch):
    store,state=reports
    from management_portal.resources.providers import report as provider
    monkeypatch.setattr(provider,'ReportStore',lambda:store)
    workspace.service._index().project_registered=lambda *args:None
    fields={'target':TARGET,'origin_key':KEY}
    registered=workspace.call('RegisterResource',{'resource_type':'report','fields':fields},idempotency_key='report-fixture-create').raise_for_error()
    identifier=registered['resource_id']
    assert identifier.startswith('report_')
    assert store.origin('alice',KEY)['report_id']==identifier
    from management_portal.home_report_store import REGISTRY_PROJECTION
    indexed=REGISTRY_PROJECTION.project(store.record(identifier),adapter='resource_registry')
    assert indexed['resource_id']==identifier
    assert indexed['title']=='汇报'
    reopened=workspace.call('ReadResource',{'address':identifier+'/revision/1'}).raise_for_error()
    assert json.loads(reopened['text'])['revision']==1
    resolution=workspace.call('ResolveResource',{'address':identifier}).raise_for_error()
    assert '/home-report?' in json.dumps(resolution)
    state['body']='更新后的输入'
    updated=workspace.call('UpdateResource',{'resource_id':identifier,'changes':{'expected_revision':1}}).raise_for_error()
    assert store.response(identifier,'alice',2)['revision']==2
    assert store.origin('alice',KEY)['revision']==1
    workspace.call('UnregisterResource',{'resource_id':identifier}).raise_for_error()
    from management_common.directory_cleanup_store import get_directory_cleanup_store
    queue=get_directory_cleanup_store()
    assert queue.status(owner_id=identifier)
    queue.run_pending()
    assert not store.directory(identifier).exists()


def test_http_origin_retry_uses_same_formal_reservation_after_interruption(workspace,reports,monkeypatch):
    from management_portal import home_report_http, resources
    from management_portal.resources.providers import report as provider
    from management_portal.resources.registry_store import RegistryWriter
    store,state=reports
    monkeypatch.setattr(provider,'ReportStore',lambda:store)
    monkeypatch.setattr(home_report_http,'ReportStore',lambda:store)
    monkeypatch.setattr(resources,'get_resource_service',lambda:workspace.service)
    workspace.service._index().project_registered=lambda *args:None
    payload={'target':TARGET,'origin_key':KEY}
    monkeypatch.setattr(home_report_http.http_request_body,'_read_bounded_json_object',lambda *a,**kw:payload)
    monkeypatch.setattr(home_report_http.http_responses,'_serve_json',lambda req,status,value:(status,value))
    monkeypatch.setattr(home_report_http.http_responses,'_serve_exception_json',lambda req,exc:(500,str(exc)))
    original=RegistryWriter.put
    def fail(writer,collection,identifier,value):
        if collection==ORIGINS: raise RuntimeError('controlled commit interruption')
        return original(writer,collection,identifier,value)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryWriter,'put',fail)
        assert home_report_http._save(None,None,'alice')[0]==500
    [directory]=[p for p in store.root.iterdir() if p.name.startswith('report_')]
    first=json.loads((directory/'1/report.json').read_text())
    state['body']='中断之后的最新来源'
    status,response=home_report_http._save(None,None,'alice')
    assert status==200,response
    assert response['report_id']==directory.name and response['version']==first['version']
    assert list(store.registry.documents(COLLECTION))==[directory.name]


def test_explicit_ppt_attachment_preserves_bytes_sources_and_old_revision(reports,monkeypatch):
    store,state=reports
    create(store)
    content=store.file(RID,1,'slides.pptx','alice')[0].read_bytes()
    from management_portal import resources
    class Service:
        def read_content(self,address,*,actor,max_bytes):
            assert actor=='alice' and address=='workspace_123456789abc/file/existing.pptx'
            return content,'application/vnd.openxmlformats-officedocument.presentationml.presentation',{}
    monkeypatch.setattr(resources,'get_resource_service',lambda:Service())
    original=store.read
    def read(name,args):
        if name=='ResolveResource':
            if state['deny']: raise PermissionError('attachment revoked')
            return {'resource':{'resource_id':'workspace_123456789abc'}}
        return original(name,args)
    store.read=read
    saved=store.attach(RID,'alice',1,'workspace_123456789abc/file/existing.pptx')
    assert saved['revision']==2
    assert store.file(RID,2,'attachment-1.pptx','alice')[0].read_bytes()==content
    assert not store.response(RID,'alice',1).get('attachments')
    assert saved['attachments'][0]['sha256']==hashlib.sha256(content).hexdigest()
    state['deny']=True
    with pytest.raises(PermissionError): store.file(RID,2,'attachment-1.pptx','alice')


def test_existing_cache_is_discovered_only_for_same_actor_and_exact_snapshot(reports):
    store,state=reports
    from management_portal.home_report import build_report
    from management_portal.home_report_media import MediaStore
    snapshot=build_report(TARGET,read=store.read)
    legacy=MediaStore()
    job='a'*64
    other=legacy.directory('bob',job);other.mkdir(parents=True)
    renderer(snapshot,other)
    legacy.save(other,{'job_id':job,'status':'ready','version':snapshot['version'],'report':snapshot,'chapters':[],'transcript':'旧媒体'})
    first=create(store)
    assert first['media'] is None
    directory=legacy.directory('alice',job);directory.mkdir(parents=True)
    renderer(snapshot,directory)
    legacy.save(directory,{'job_id':job,'status':'ready','version':snapshot['version'],'report':snapshot,'chapters':[],'transcript':'旧媒体'})
    recovered=create(store,KEY[:-1]+'d','report_123456789abd')
    assert recovered['media']['status']=='ready'
    assert recovered['media']['imported_from']['job_id']==job
    assert store.file('report_123456789abd',1,'video.mp4','alice')[0].read_bytes()==(directory/'video.mp4').read_bytes()
    state['body']='不同请求内容'
    unmatched=create(store,KEY[:-1]+'e','report_123456789abe')
    assert unmatched['media'] is None


def test_creation_keeps_user_initiator_and_syncs_permissions_in_own_transaction(reports,monkeypatch):
    store,state=reports
    from management_common.execution_association import execution_scope
    from management_portal.society_service import resident_work_origin
    from management_portal import permission_system
    resident='resident_000000000001'
    monkeypatch.setattr(resident_work_origin,'execution_initiator',lambda rid,execution_id:'bob')
    captured=[]
    def sync(record,*,resource_type,connection):
        assert connection.in_transaction
        captured.append(record)
    monkeypatch.setattr(permission_system,'sync_resource',sync)
    with execution_scope('report-input-fixture'):
        record=store.create(RID,{'target':TARGET},resident)
    assert record['created_by']==resident and record['created_for']=='bob'
    assert captured[0]['created_for']=='bob'


def test_media_lease_is_visible_across_processes_and_process_exit_allows_retry(reports):
    store,state=reports
    create(store)
    media=PersistentMedia(store,renderer)
    pending=store.directory(RID)/'1/media-state.json'
    pending.write_text(json.dumps({'status':'pending','version':store.response(RID,'alice')['version']}))
    child=subprocess.Popen([sys.executable,'-c',
        "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()",
        str(media.lease_path(RID,1))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='locked'
        assert media.status(RID,1,'alice')['status']=='pending'
        assert media.start(RID,1,'alice')['status']=='pending'
    finally:
        child.communicate(timeout=5)
    assert media.status(RID,1,'alice')['status']=='failed'
    media.start(RID,1,'alice',retry=True)
    assert wait_ready(media)['status']=='ready'


def test_nested_directory_cleanup_retries_after_unregister_and_stale_delete_is_safe(reports,monkeypatch):
    store,state=reports
    create(store)
    from management_portal.resources.workspace_store import get_workspace_store
    nested=store.directory(RID)/'nested'
    nested.mkdir()
    wid='workspace_aaaaaaaaaaaa'
    workspaces=get_workspace_store()
    workspaces.ensure(wid,str(nested),owner_id='unrelated-owner')
    store.refresh(RID,'alice',1)
    with pytest.raises(ResourceVersionConflict): store.delete(RID,'alice',1)
    assert store.record(RID)['revision']==2
    deleted=store.delete(RID,'alice',2)
    assert not workspaces.rows([wid])
    from management_common import directory_cleanup_store
    queue=directory_cleanup_store.get_directory_cleanup_store()
    cleanup_id=deleted['directory_cleanup']['cleanup_ids'][0]
    def failed(path): raise OSError('controlled filesystem failure')
    with monkeypatch.context() as patch:
        patch.setattr(directory_cleanup_store,'remove_path',failed)
        queue.run_pending(cleanup_id=cleanup_id)
    assert queue.status(cleanup_id=cleanup_id)[0]['status']=='retry'
    with pytest.raises(FileNotFoundError): store.get(RID,'alice')
    # Advance only this disposable fixture's queue deadline; retry is the normal worker.
    with queue.database.transaction() as connection:
        connection.execute(f'UPDATE {queue.table} SET available_at=clock_timestamp() WHERE cleanup_id=%s',(cleanup_id,))
    queue.run_pending()
    assert queue.status(cleanup_id=cleanup_id)[0]['status']=='completed'
    assert not store.directory(RID).exists()


def test_actual_par_report_actions_and_read_revocation(reports,monkeypatch):
    from tests.cases.permission_test_support import enable
    from management_portal.home_report_store import require_permission
    from management_portal import permission_system
    store,state=reports
    permissions=enable(monkeypatch)
    try:
        permissions.users('alice','bob',category='intern')
        store.authorize=require_permission
        create(store)
        assert store.record(RID)['created_by']=='alice'
        assert store.get(RID,'alice')[1]==1
        # Default shared discovery does not grant mutation rights. The report
        # owner obeys the active scheme instead of inventing a creator ACL.
        with pytest.raises(PermissionError): store.authorize('alice','UpdateResource',RID)
        permissions.grant('alice',RID,a='@admin')
        store.authorize('alice','UpdateResource',RID)
        with pytest.raises(PermissionError): store.authorize('bob','UpdateResource',RID)
        permissions.deny('alice',RID)
        with pytest.raises(PermissionError): store.response(RID,'alice',1)
        with pytest.raises(PermissionError): store.file(RID,1,'report.html','alice')
    finally:
        permission_system.forget_implementation()

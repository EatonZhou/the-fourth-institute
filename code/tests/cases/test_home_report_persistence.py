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
from tests.cases.test_report_explanations import explanations
from tests.cases.historical_report_support import seed_historical_report

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
    """Install a historical snapshot, then exercise current public readers."""
    seed_historical_report(store, identifier, {'target': TARGET, 'origin_key': key})
    return store.response(identifier, 'alice')


def test_origin_returns_first_snapshot_when_sources_change_and_is_actor_scoped(reports):
    store,state = reports
    first = create(store)
    state['body'] = '新的业务事实'
    second = store.reopen_origin({'target':TARGET,'origin_key':KEY,'version':'outdated'},'alice')
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


def test_report_and_origin_commit_together_on_interrupted_creation(explanations, monkeypatch):
    from management_portal.home_report_explanations import fields
    from management_portal.home_report_store import EXPLANATIONS
    from management_portal.resources.registry_store import RegistryWriter
    e = explanations
    store = e.store
    payload = fields(e.ref)
    original = RegistryWriter.put
    def fail(writer, collection, identifier, value):
        if collection == ORIGINS:
            raise RuntimeError('simulated before origin commit')
        return original(writer, collection, identifier, value)
    with monkeypatch.context() as patch:
        patch.setattr(RegistryWriter, 'put', fail)
        with pytest.raises(RuntimeError):
            store.create(RID, payload, 'alice')
    assert store.registry.get(COLLECTION, RID) is None
    assert store.registry.get(ORIGINS, store.origin_id('alice', payload['origin_key'])) is None
    assert store.registry.get(EXPLANATIONS, payload['origin_key']) is None
    original_snapshot = json.loads((store.directory(RID)/'1/report.json').read_text())
    restored = store.create(RID, payload, 'alice')
    assert restored['resource_id'] == RID
    snapshot = store.response(RID, 'alice')
    assert snapshot['version'] == original_snapshot['version']
    assert snapshot['checked_at'] == original_snapshot['checked_at']
    assert snapshot['explanation_target'] == e.ref
    assert list(store.record(RID)['revisions']) == ['1']


@pytest.mark.parametrize('operation', ['refresh', 'attach'])
def test_revision_retry_after_registry_rollback_preserves_committed_versions(reports, monkeypatch, operation):
    store, state = reports
    first = create(store)
    original_bytes = store.file(RID, 1, 'report.json', 'alice')[0].read_bytes()
    if operation == 'attach':
        from management_portal import resources
        content = store.file(RID, 1, 'slides.pptx', 'alice')[0].read_bytes()

        class Service:
            def read_content(self, address, *, actor, max_bytes):
                return content, 'application/vnd.openxmlformats-officedocument.presentationml.presentation', {}

        monkeypatch.setattr(resources, 'get_resource_service', lambda: Service())
        source_read = store.read
        store.read = lambda name, args: {'resource': {}} if name == 'ResolveResource' else source_read(name, args)

    def append():
        if operation == 'attach':
            return store.attach(RID, 'alice', 1, 'workspace_123456789abc/file/existing.pptx')
        return store.refresh(RID, 'alice', 1)

    from management_portal.resources.registry_store import RegistryWriter
    original_put = RegistryWriter.put

    def fail(writer, collection, identifier, value):
        result = original_put(writer, collection, identifier, value)
        if collection == COLLECTION and identifier == RID and value['revision'] == 2:
            raise RuntimeError('interrupted revision registration')
        return result

    with monkeypatch.context() as patch:
        patch.setattr(RegistryWriter, 'put', fail)
        with pytest.raises(RuntimeError, match='interrupted revision registration'):
            append()
    assert store.record(RID)['revision'] == 1
    with pytest.raises(FileNotFoundError):
        store.get(RID, 'alice', 2)
    state['body'] = 'Retry sees new source facts'
    append()
    assert store.record(RID)['revision'] == 2
    assert store.origin('alice', KEY)['version'] == first['version']
    assert store.file(RID, 1, 'report.json', 'alice')[0].read_bytes() == original_bytes
    if operation == 'attach':
        assert store.file(RID, 2, 'attachment-1.pptx', 'alice')[0].read_bytes() == content
    else:
        assert store.get(RID, 'alice', 2)[2]['rows'][0]['request_text'] == state['body']
    committed = store.file(RID, 2, 'report.json', 'alice')[0].read_bytes()
    with pytest.raises(ResourceVersionConflict):
        append()
    assert store.file(RID, 2, 'report.json', 'alice')[0].read_bytes() == committed


def test_revision_retry_rejects_redirected_uncommitted_directory(reports, tmp_path):
    store, state = reports
    create(store)
    external = tmp_path / 'external'
    external.mkdir()
    sentinel = external / 'keep.txt'
    sentinel.write_text('unrelated content')
    (store.directory(RID) / '2').symlink_to(external, target_is_directory=True)
    with pytest.raises(PermissionError, match='符号链接'):
        store.refresh(RID, 'alice', 1)
    assert sentinel.read_text() == 'unrelated content'
    assert store.record(RID)['revision'] == 1


def renderer(report,directory):
    for filename in FILES:
        (directory / filename).write_bytes((filename+report['version']).encode())
    return {'duration':1,'chapters':[],'transcript':'测试音轨接口','generation_seconds':0.01}


def wait_ready(media, quality='medium'):
    for _ in range(300):
        result = media.status(RID,1,'alice',quality=quality)
        if result and result['status'] != 'pending':
            assert result['status'] == 'ready',result
            return result
        time.sleep(.01)
    pytest.fail('media did not complete')


def test_media_survives_store_recreation_and_requires_current_permissions(reports):
    store,state=reports
    create(store)
    media=PersistentMedia(store,renderer)
    media.start(RID,1,'alice',quality='low')
    result=wait_ready(media,'low')
    first=media.file(RID,1,'video.mp4','alice',quality='low')[0].read_bytes()
    recreated=ReportStore(root=store.root,authorize=store.authorize,read=store.read)
    restored=PersistentMedia(recreated).status(RID,1,'alice',quality='low')
    assert restored['assets']['video.mp4'] == hashlib.sha256(first).hexdigest()
    assert recreated.file(RID,1,'low-video.mp4','alice')[0].read_bytes()==first
    assert PersistentMedia(recreated,lambda *args:pytest.fail('must not regenerate')).start(RID,1,'alice',quality='low')['status']=='ready'
    state['deny_report']=True
    with pytest.raises(PermissionError): recreated.response(RID,'alice',1)
    state['deny_report']=False
    state['deny']=True
    with pytest.raises(PermissionError): recreated.file(RID,1,'low-video.mp4','alice')


@pytest.mark.parametrize('damage', ['modified', 'missing', 'untracked'])
def test_persisted_media_damage_is_not_served_or_regenerated(reports, damage):
    store, _ = reports
    create(store)
    media = PersistentMedia(store, renderer)
    media.start(RID, 1, 'alice', quality='low')
    wait_ready(media,'low')
    path = media.file(RID, 1, 'video.mp4', 'alice', quality='low')[0]
    if damage == 'modified':
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    elif damage == 'missing':
        path.unlink()
    else:
        state_path = path.parent / 'state.json'
        state = json.loads(state_path.read_text())
        del state['assets']['video.mp4']
        state_path.write_text(json.dumps(state))
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(FileNotFoundError, match='媒体资产'):
        store.file(RID, 1, 'low-video.mp4', 'alice')
    assert (path.read_bytes() if path.exists() else None) == before
    assert store.record(RID)['revision'] == 1


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
    media.start(RID,1,'alice',quality='low')
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
        if not business_tasks.is_active(media.task(RID,1,'low')): break
        time.sleep(.01)
    assert not (store.directory(RID)/'1/media-low').exists()
    from management_portal.resources.errors import ResourceOperationError
    with pytest.raises(ResourceOperationError,match='已删除'): store.origin('alice',KEY)


def test_formal_resource_registration_reopen_and_directory_cleanup(workspace,reports,monkeypatch):
    store,state=reports
    from management_portal.resources.providers import report as provider
    monkeypatch.setattr(provider,'ReportStore',lambda:store)
    workspace.service._index().project_registered=lambda *args:None
    # Existing snapshot lifecycle remains a Resource ISA responsibility.
    create(store)
    identifier=RID
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


def test_http_origin_retry_uses_same_formal_reservation_after_interruption(explanations, monkeypatch):
    from types import SimpleNamespace
    from management_portal import home_report_http
    from management_portal.resources.registry_store import RegistryWriter
    e = explanations
    store = e.store
    monkeypatch.setattr(home_report_http.http_request_body, '_read_bounded_json_object', lambda *a, **kw: e.ref)
    original = RegistryWriter.put
    def fail(writer, collection, identifier, value):
        if collection == ORIGINS:
            raise RuntimeError('controlled commit interruption')
        return original(writer, collection, identifier, value)
    request = SimpleNamespace(command='POST')
    with monkeypatch.context() as patch:
        patch.setattr(RegistryWriter, 'put', fail)
        assert home_report_http._explanations(request, None, 'alice')[0] == 500
    [directory] = [p for p in store.root.iterdir() if p.name.startswith('report_')]
    first = json.loads((directory/'1/report.json').read_text())
    status, response = home_report_http._explanations(request, None, 'alice')
    assert status == 200, response
    assert response['report_id'] == directory.name and response['version'] == first['version']
    assert response['explanation_target'] == e.ref
    assert list(store.registry.documents(COLLECTION)) == [directory.name]


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


@pytest.mark.parametrize('legacy_audio',[False,True],ids=['silent-cache','existing-audio-cache'])
def test_existing_cache_is_discovered_only_for_same_actor_and_exact_snapshot(reports,legacy_audio,caplog):
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
    if not legacy_audio: (directory/'narration.wav').unlink()
    legacy.save(directory,{'job_id':job,'status':'ready','version':snapshot['version'],'report':snapshot,'chapters':[],'transcript':'旧媒体'})
    recovered=create(store,KEY[:-1]+'d','report_123456789abd')
    assert recovered['media']['status']=='ready'
    assert recovered['media']['imported_from']['job_id']==job
    assert store.file('report_123456789abd',1,'video.mp4','alice')[0].read_bytes()==(directory/'video.mp4').read_bytes()
    if legacy_audio:
        assert store.file('report_123456789abd',1,'narration.wav','alice')[0].read_bytes()==(directory/'narration.wav').read_bytes()
    else:
        assert 'narration.wav' not in recovered['media']['assets']
        with pytest.raises(FileNotFoundError):store.file('report_123456789abd',1,'narration.wav','alice')
    state['body']='不同请求内容'
    unmatched=create(store,KEY[:-1]+'e','report_123456789abe')
    assert unmatched['media'] is None
    assert 'Skipped unavailable or incompatible legacy report media cache' in caplog.text
    assert str(directory) not in caplog.text


@pytest.mark.parametrize('failure', [PermissionError, FileNotFoundError])
def test_project_report_warns_when_references_cannot_be_read(failure):
    from management_portal.home_report import ReportReader
    def read(name, args):
        if name == 'GetProject':
            return {'project': {'title': '研究项目'}}
        if name == 'GetResearchMap':
            return {'directions': [{'direction_id': 'd1', 'title': '方向'}],
                    'outputs': {'d1': [{'resource_id': 'unreadable'}, {'resource_id': 'readable'}]},
                    'notes': [{'direction_id': 'd1', 'refs': ['unreadable'], 'text': 'private note'},
                              {'direction_id': 'd1', 'refs': ['readable'], 'text': '可读记录'}]}
        if name == 'ResolveResource':
            if args['address'] == 'unreadable':
                raise failure('private-source-detail')
            return {'resource': {'title': '可读成果'}}
        raise AssertionError(name)
    reader = ReportReader(read)
    _, _, rows, _ = reader.project('project')
    assert len(rows[0]['artifacts']) == 1
    assert rows[0]['notes'] == ['可读记录']
    assert len(reader.warnings) == 2
    assert all('不可读' in warning for warning in reader.warnings)
    assert 'private-source-detail' not in json.dumps(reader.warnings)
    assert 'private note' not in json.dumps(rows)


def test_creation_keeps_user_initiator_and_syncs_permissions_in_own_transaction(explanations,monkeypatch):
    store=explanations.store
    from management_common.execution_association import execution_scope
    from management_portal.society_service import resident_work_origin
    from management_portal import permission_system, society_service
    from types import SimpleNamespace
    resident='resident_000000000001'
    capability_calls=[]
    def creation_allowed(responsible, actor, message_id=''):
        capability_calls.append((responsible, actor, message_id))
        return (responsible, actor, message_id) == (resident, resident, '')
    monkeypatch.setattr(society_service, 'get_society_service', lambda: SimpleNamespace(
        resident=SimpleNamespace(reporting_creation_allowed=creation_allowed)))
    monkeypatch.setattr(resident_work_origin,'execution_initiator',lambda rid,execution_id:'bob')
    captured=[]
    def sync(record,*,resource_type,connection):
        assert connection.in_transaction
        captured.append(record)
    monkeypatch.setattr(permission_system,'sync_resource',sync)
    from tests.cases.test_workspace_resource_rename import WID
    from management_portal.permission_system.facts import Fact
    explanations.permissions.place(Fact('resident',resident))
    explanations.permissions.grant(resident,WID,'@viewer')
    with execution_scope('report-input-fixture'):
        record=store.create(RID,{'explanation_target':explanations.ref},resident)
    assert record['created_by']==resident and record['created_for']=='bob'
    assert captured[0]['created_for']=='bob'
    assert capability_calls == []  # Registering an existing package is not new creation.


def test_media_lease_is_visible_across_processes_and_process_exit_allows_retry(reports):
    store,state=reports
    create(store)
    media=PersistentMedia(store,renderer)
    pending=store.directory(RID)/'1/media-low-state.json'
    pending.write_text(json.dumps({'status':'pending','version':store.response(RID,'alice')['version'],'quality':'low'}))
    child=subprocess.Popen([sys.executable,'-c',
        "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()",
        str(media.lease_path(RID,1,'low'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='locked'
        assert media.status(RID,1,'alice',quality='low')['status']=='pending'
        assert media.start(RID,1,'alice',quality='low')['status']=='pending'
    finally:
        child.communicate(timeout=5)
    assert media.status(RID,1,'alice',quality='low')['status']=='failed'
    media.start(RID,1,'alice',retry=True,quality='low')
    assert wait_ready(media,'low')['status']=='ready'


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
        # Formal creation places a User's report under their personal space.
        # Creator mutation rights come from PAR, while outsiders stay denied.
        store.authorize('alice','UpdateResource',RID)
        with pytest.raises(PermissionError): store.authorize('bob','UpdateResource',RID)
        permissions.grant('bob',RID,a='@contributor')
        store.authorize('bob','UpdateResource',RID)
        permissions.deny('bob',RID,a='@contributor')
        with pytest.raises(PermissionError): store.authorize('bob','UpdateResource',RID)
        permissions.deny('alice',RID)
        with pytest.raises(PermissionError): store.response(RID,'alice',1)
        with pytest.raises(PermissionError): store.file(RID,1,'report.html','alice')
    finally:
        permission_system.forget_implementation()
